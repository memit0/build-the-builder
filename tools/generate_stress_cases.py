"""Expand the supported practice cases into reusable conversation stress suites.

The practice service accepts only the case IDs in public-test-set.json. These
suites keep those bank outcomes intact and vary how the simulated customer
expresses the same facts, using the styles supported by tools/run_cases.py.
"""
import copy
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
STYLES = ("pieces", "corrects", "talkative", "asks_back", "in_words")


def main():
    cases = json.loads((ROOT / "public-test-set.json").read_text())
    out = ROOT / "stress-tests"
    out.mkdir(exist_ok=True)
    for style in STYLES:
        suite = []
        for original in cases:
            case = copy.deepcopy(original)
            case["style"] = style
            case["title"] += f" [stress: {style}]"
            suite.append(case)
        path = out / f"cases-{style}.json"
        path.write_text(json.dumps(suite, indent=2, ensure_ascii=False) + "\n")
        print(f"{path.relative_to(ROOT)}: {len(suite)} cases")


if __name__ == "__main__":
    main()
