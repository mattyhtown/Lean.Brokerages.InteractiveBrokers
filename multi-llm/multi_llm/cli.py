from __future__ import annotations

import argparse
import json
import sys

from multi_llm.client import ChatRequest, LLMError, complete, fanout_complete
from multi_llm.mcp_templates import SUPPORTED_TARGETS, render_mcp_config
from multi_llm.providers import list_provider_statuses


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="multi-llm",
        description="Unified multi-LLM CLI (47 registered providers) + MCP config generator",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_prov = sub.add_parser("providers", help="List all supported LLM providers and credential status")
    p_prov.add_argument("--json", action="store_true", help="Output JSON")

    p_mcp = sub.add_parser("mcp-config", help="Generate key-free MCP config for any LLM client or API")
    p_mcp.add_argument(
        "--target",
        required=True,
        choices=SUPPORTED_TARGETS,
        help="Target LLM client or API schema",
    )

    p_chat = sub.add_parser("chat", help="Send a prompt to a specific LLM provider")
    p_chat.add_argument("--provider", required=True, help="Provider id or alias (e.g. anthropic, openai, gemini, xai)")
    p_chat.add_argument("--model", default=None, help="Override default model id")
    p_chat.add_argument("--system", default=None, help="Optional system instruction")
    p_chat.add_argument("--prompt", required=True, help="User prompt")
    p_chat.add_argument("--max-tokens", type=int, default=1024, help="Max output tokens")
    p_chat.add_argument("--temperature", type=float, default=0.2, help="Sampling temperature")
    p_chat.add_argument(
        "--attach-mcp",
        default="",
        help="Comma-separated remote MCPs for anthropic/openai ('uw', 'supabase')",
    )
    p_chat.add_argument("--json", action="store_true", help="Emit structured JSON output")

    p_fan = sub.add_parser("fanout", help="Run a prompt across multiple LLMs concurrently")
    p_fan.add_argument(
        "--providers",
        default="",
        help="Comma-separated provider list (e.g. anthropic,openai,gemini,xai)",
    )
    p_fan.add_argument(
        "--all-configured",
        action="store_true",
        help="Query every provider whose credentials are set in the environment",
    )
    p_fan.add_argument("--system", default=None, help="Optional system instruction")
    p_fan.add_argument("--prompt", required=True, help="User prompt")
    p_fan.add_argument("--max-tokens", type=int, default=1024, help="Max output tokens")
    p_fan.add_argument("--temperature", type=float, default=0.2, help="Sampling temperature")

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "providers":
            statuses = list_provider_statuses()
            if args.json:
                print(json.dumps(statuses, indent=2))
                return
            for row in statuses:
                state = "configured" if row["configured"] else "missing-key"
                mcp_flag = " [remote-mcp]" if row["supports_remote_mcp"] else ""
                envs = "/".join(row["api_key_envs"])  # type: ignore[arg-type]
                print(
                    f"{row['provider']:<14} {state:<12} model={row['default_model']:<42} env={envs}{mcp_flag}"
                )
            return

        if args.command == "mcp-config":
            print(render_mcp_config(args.target))
            return

        if args.command == "chat":
            mcp_tuple = tuple(part.strip() for part in args.attach_mcp.split(",") if part.strip())
            resp = complete(
                ChatRequest(
                    provider=args.provider,
                    model=args.model,
                    system=args.system,
                    prompt=args.prompt,
                    max_tokens=args.max_tokens,
                    temperature=args.temperature,
                    attach_mcp=mcp_tuple,
                )
            )
            if args.json:
                print(
                    json.dumps(
                        {"provider": resp.provider, "model": resp.model, "text": resp.text},
                        indent=2,
                        ensure_ascii=False,
                    )
                )
            else:
                print(resp.text)
            return

        if args.command == "fanout":
            selected = [part.strip() for part in args.providers.split(",") if part.strip()]
            if args.all_configured:
                selected = [str(row["provider"]) for row in list_provider_statuses() if row["configured"]]
            if not selected:
                raise LLMError("No providers selected (pass --providers or configure keys for --all-configured)")
            results = fanout_complete(
                providers=selected,
                prompt=args.prompt,
                system=args.system,
                max_tokens=args.max_tokens,
                temperature=args.temperature,
            )
            print(json.dumps(results, indent=2, ensure_ascii=False))
            return

    except (LLMError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
