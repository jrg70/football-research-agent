"""A small research agent: give it a betting question, it investigates with tools.

The whole "agent" is the loop in `run()`: send the conversation to Claude, run
whatever tools it asks for, append the results, repeat until it stops asking.
Claude decides which tools to call, in what order, and when it has enough.

    python agent.py "Is there any edge betting home underdogs in the Bundesliga?"
"""

import argparse
import json
import sys

import anthropic

from tools import TOOLS, run_tool

MODEL = "claude-opus-5-5"
MAX_TURNS = 25

SYSTEM_PROMPT = """You are a skeptical sports-betting research analyst. You answer questions \
about football betting markets using only the tools provided, which work on historical \
football-data.co.uk results and closing odds.

How to work:
- Start by checking which data is available.
- Form a concrete hypothesis, then test it with backtests. Prefer a few well-chosen tests \
over many random ones.
- Guard against overfitting. If you pick a rule (a selection plus a probability range) \
after looking at some seasons, re-test the exact same rule on seasons you did not use to \
choose it. Only a rule that holds out-of-sample is worth reporting as a possible edge.
- Closing lines are hard to beat. Expect most ideas to fail, and say so plainly when they \
do. A well-supported "no edge" is a good result.
- Be clear about sample size and confidence intervals. Treat best-price ("max") odds as \
optimistic.

When you are done, call write_report once with a concise Markdown report: the question, \
what you tested (with numbers), what held up out-of-sample, and a one-line verdict. Then \
reply with a short summary."""


def _print_step(turn, block):
    args = json.dumps(block.input)
    print(f"\n[turn {turn}] -> {block.name}({args[:200]}{'...' if len(args) > 200 else ''})")


def run(question, client=None, max_turns=MAX_TURNS, verbose=True):
    client = client or anthropic.Anthropic()
    messages = [{"role": "user", "content": question}]

    for turn in range(1, max_turns + 1):
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
            output_config={"effort": "high"},
            # If a safety classifier declines, retry on a suitable fallback model.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        # Append the full content (thinking + tool calls) unchanged; the history stays append-only.
        messages.append({"role": "assistant", "content": response.content})

        if verbose:
            for block in response.content:
                if block.type == "text" and block.text.strip() and response.stop_reason == "tool_use":
                    print(f"\n[turn {turn}] {block.text.strip()}")

        if response.stop_reason == "refusal":
            raise RuntimeError("The model declined this request.")
        if response.stop_reason == "max_tokens":
            raise RuntimeError("Response hit max_tokens before finishing a step.")
        if response.stop_reason != "tool_use":
            return "".join(b.text for b in response.content if b.type == "text")

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            if verbose:
                _print_step(turn, block)
            output, is_error = run_tool(block.name, block.input)
            if verbose:
                print(f"         <- {output[:300]}{'...' if len(output) > 300 else ''}")
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": output,
                "is_error": is_error,
            })
        # All results from one turn go back in a single user message.
        messages.append({"role": "user", "content": tool_results})

    raise RuntimeError(f"Agent did not finish within {max_turns} turns.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("question", help="The research question, in plain English.")
    parser.add_argument("--max-turns", type=int, default=MAX_TURNS)
    args = parser.parse_args()

    try:
        answer = run(args.question, max_turns=args.max_turns)
    except anthropic.AuthenticationError:
        sys.exit("No valid credentials. Set ANTHROPIC_API_KEY or run `ant auth login`.")
    except anthropic.RateLimitError:
        sys.exit("Rate limited by the API. Wait a bit and try again.")
    except anthropic.APIConnectionError:
        sys.exit("Could not reach the Anthropic API. Check your connection.")
    except RuntimeError as e:
        sys.exit(str(e))
    print("\n" + "=" * 60 + "\n" + answer)


if __name__ == "__main__":
    main()
