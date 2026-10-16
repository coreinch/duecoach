"""Run the replay cases against the real model: python -m evals [--runs 3] [case-name ...]

Each case runs --runs times in a throwaway database; every run must pass (the model is not deterministic, so a case that fails once in
three is a real weakness). Needs LLM_API_KEY (and LLM_BASE_URL / LLM_MODEL if they are not the defaults). Costs real model calls.
"""

import argparse
import asyncio
import os
import sys
import tempfile

from duecoach import db, llm  # noqa: E402

from .cases import CASES, UID, Case


async def run_once(case: Case) -> list[str]:
    """Run one case in a fresh database; returns what failed (empty: it passed)."""
    with tempfile.TemporaryDirectory() as folder:
        db.init(os.path.join(folder, "evals.db"))
        try:
            db.get_or_create_user("telegram", str(UID), str(UID), "en")
            db.set_fields(UID, consent_at=1.0, tz_set=1, tz="Europe/Athens", interval_min=30)
            case.seed()
            try:
                reply = await llm.reply(UID, case.user_text, case.instruction)
            except llm.ModelError as error:
                if case.user_text:
                    return [f"the model failed: {error}"]
                return []  # a check-in the verifier would not let through is a pass: nothing was sent
            failed = [f"database: {what}" for what, test in case.database if not test()]
            for question in case.judge:
                answer = await llm._complete(
                    [
                        {
                            "role": "user",
                            "content": f'Message from a coaching bot:\n"""{reply}"""\n\n{question}\nAnswer with one word: YES or NO.',
                        }
                    ],
                    max_tokens=llm.VERIFY_TOKENS,
                    model=llm.LLM_VERIFIER_MODEL,
                )
                if answer.strip().upper().startswith("YES"):
                    failed.append(f"judge said YES to: {question}  (reply: {reply!r})")
            return failed
        finally:
            db.close()


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals")
    parser.add_argument("names", nargs="*", help="only these cases")
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args(argv)
    chosen = [c for c in CASES if not args.names or c.name in args.names]
    if not chosen:
        print("no such case; known:", ", ".join(c.name for c in CASES))
        return 2
    failures = 0
    for case in chosen:
        problems = [await run_once(case) for _ in range(args.runs)]
        passed = sum(1 for p in problems if not p)
        print(f"{'PASS' if passed == args.runs else 'FAIL'} {case.name}: {passed}/{args.runs}")
        for run in problems:
            for line in run:
                print(f"    {line}")
        failures += passed != args.runs
    print(f"\n{len(chosen) - failures}/{len(chosen)} cases passed on every run")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
