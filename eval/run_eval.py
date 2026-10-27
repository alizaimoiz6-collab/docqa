"""Run a fixed question set against a DocQA deployment and summarize the results.

    python eval/run_eval.py --api https://<workspace>--docqa-api.us-east.modal.run

Writes eval/results.json (every response) and eval/results.md (summary table).
Answer correctness is a keyword check: each group in `expect` needs at least one
of its alternatives to appear in the answer. Unanswerable questions pass when the
API returns answerable=false.
"""

import argparse
import json
import statistics
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent

# USD per million tokens for claude-opus-5-5 (list prices; cache write is 1.25x input).
PRICE = {"input": 4.00, "output": 20.00, "cache_read": 0.20, "cache_write": 5.00}

CASES = {
    "sample_docs/acme_policy.pdf": [
        ("What is the copay for outpatient visits?", [["25"]]),
        ("How long do I have to submit a claim?", [["90"]]),
        ("When is the ER copay waived?", [["admit"]]),
        ("Does the policy cover prescription drugs?", None),
    ],
    "sample_docs/remote_work_policy.txt": [
        ("What equipment or allowance does the company provide for remote work?", [["laptop"], ["500"]]),
        ("What are the core hours for remote employees?", [["10"], ["15"]]),
        ("How quickly must a lost device be reported?", [["24"]]),
        ("How many vacation days do employees get?", None),
    ],
    "sample_docs/claims_handling_manual.md": [
        ("What makes a claim 'clean'?", [["member id"], ["npi"]]),
        ("What is the filing deadline for providers versus members?", [["90"], ["180"]]),
        ("Who must approve a claim over $100,000?", [["director"]]),
        ("What happens if a service that needs prior authorization was performed without it?", [["30"], ["d-15"]]),
        ("When does interest accrue on clean claims that are decided late, and at what rate?", [["45"], ["1%", "1 percent"]]),
        ("Which fraud indicators require an SIU referral?", [["death"], ["24 hours"], ["altered"]]),
        ("How long are claim files retained?", [["10 years", "ten years"]]),
        ("How long does someone have to request a second-level appeal review?", [["60"]]),
        ("What is the turnaround time for dental claims?", None),
        ("Who is the CEO of Northwind Mutual?", None),
    ],
}


def cost(usage: dict) -> tuple[float, float]:
    """(actual cost, cost if nothing had been cached) in USD."""
    actual = (
        usage["input_tokens"] * PRICE["input"]
        + usage["output_tokens"] * PRICE["output"]
        + usage["cache_read_input_tokens"] * PRICE["cache_read"]
        + usage.get("cache_creation_input_tokens", 0) * PRICE["cache_write"]
    ) / 1e6
    all_input = usage["input_tokens"] + usage["cache_read_input_tokens"] + usage.get("cache_creation_input_tokens", 0)
    uncached = (all_input * PRICE["input"] + usage["output_tokens"] * PRICE["output"]) / 1e6
    return actual, uncached


def post_with_retry(url: str, **kwargs) -> requests.Response:
    for _ in range(5):
        resp = requests.post(url, timeout=180, **kwargs)
        if resp.status_code != 429:
            return resp
        wait = int(resp.headers.get("Retry-After", "10"))
        print(f"  rate limited, waiting {wait}s")
        time.sleep(wait)
    return resp


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", required=True)
    parser.add_argument("--pause", type=float, default=6.5, help="seconds between questions (API allows 10/min)")
    args = parser.parse_args()
    api = args.api.rstrip("/")

    rows = []
    for path, questions in CASES.items():
        file = ROOT / path
        up = post_with_retry(f"{api}/upload", files={"file": (file.name, file.read_bytes())})
        up.raise_for_status()
        doc_id = up.json()["doc_id"]
        print(f"{file.name}: {doc_id}")

        for i, (question, expect) in enumerate(questions):
            started = time.perf_counter()
            resp = post_with_retry(f"{api}/ask", json={"doc_id": doc_id, "question": question})
            wall_ms = int((time.perf_counter() - started) * 1000)
            body = resp.json()
            if not resp.ok:
                print(f"  ERROR {resp.status_code}: {body}")
                rows.append({"doc": file.name, "question": question, "error": body, "status": resp.status_code})
                continue

            answer = body["answer"].lower()
            if expect is None:
                correct = body["answerable"] is False
            else:
                correct = body["answerable"] and all(any(alt in answer for alt in group) for group in expect)
            actual, uncached = cost(body["usage"])
            rows.append(
                {
                    "doc": file.name,
                    "question": question,
                    "expected_answerable": expect is not None,
                    "correct": correct,
                    "first_question_on_doc": i == 0,
                    "wall_ms": wall_ms,
                    "cost_usd": actual,
                    "uncached_cost_usd": uncached,
                    "response": body,
                }
            )
            cits = body["citations"]
            print(
                f"  {'PASS' if correct else 'FAIL'} {body['latency_ms'] / 1000:4.1f}s "
                f"{sum(c['verified'] for c in cits)}/{len(cits)} cites  {question}"
            )
            time.sleep(args.pause)

    (ROOT / "eval/results.json").write_text(json.dumps(rows, indent=2))
    summary = summarize(rows)
    (ROOT / "eval/results.md").write_text(summary)
    print("\n" + summary)


def summarize(rows: list[dict]) -> str:
    ok = [r for r in rows if "error" not in r]
    answerable = [r for r in ok if r["expected_answerable"]]
    unanswerable = [r for r in ok if not r["expected_answerable"]]
    cits = [c for r in ok for c in r["response"]["citations"]]
    model_s = sorted(r["response"]["latency_ms"] / 1000 for r in ok)
    wall_s = sorted(r["wall_ms"] / 1000 for r in ok)
    first = [r["cost_usd"] for r in ok if r["first_question_on_doc"]]
    follow = [r["cost_usd"] for r in ok if not r["first_question_on_doc"]]
    total, total_uncached = sum(r["cost_usd"] for r in ok), sum(r["uncached_cost_usd"] for r in ok)

    def p90(xs):
        return xs[min(len(xs) - 1, int(round(0.9 * (len(xs) - 1))))]

    lines = [
        "| Metric | Value |",
        "|---|---|",
        f"| Questions | {len(rows)} ({len(answerable)} answerable, {len(unanswerable)} not in document), {len(rows) - len(ok)} errors |",
        f"| Answerable questions answered correctly | {sum(r['correct'] for r in answerable)}/{len(answerable)} |",
        f"| Unanswerable questions correctly declined | {sum(r['correct'] for r in unanswerable)}/{len(unanswerable)} |",
        f"| Citations verified verbatim | {sum(c['verified'] for c in cits)}/{len(cits)} ({100 * sum(c['verified'] for c in cits) / max(len(cits), 1):.0f}%) |",
        f"| Model latency, median / p90 | {statistics.median(model_s):.1f}s / {p90(model_s):.1f}s |",
        f"| End-to-end latency, median / p90 | {statistics.median(wall_s):.1f}s / {p90(wall_s):.1f}s |",
        f"| Avg cost per question | ${total / len(ok):.4f} |",
        f"| Avg cost, first question on a document | ${statistics.mean(first):.4f} |",
        f"| Avg cost, follow-up questions (cached) | ${statistics.mean(follow):.4f} |",
        f"| Total saved by prompt caching | {100 * (1 - total / total_uncached):.0f}% (${total:.3f} vs ${total_uncached:.3f}) |",
    ]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
