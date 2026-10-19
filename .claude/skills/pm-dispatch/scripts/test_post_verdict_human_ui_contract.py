from __future__ import annotations

from pathlib import Path
import unittest


SKILLS = Path(__file__).resolve().parents[2]
CANONICAL = SKILLS / "default-mode" / "references" / "project-session-contract.md"
PM_DISPATCH = SKILLS / "pm-dispatch" / "SKILL.md"
COMPLETE = SKILLS / "complete-milestone" / "SKILL.md"
REVIEW = SKILLS / "codex-verify" / "SKILL.md"


class PostVerdictHumanUiContractTest(unittest.TestCase):
    def test_human_blocked_is_the_only_bounded_post_verdict_ui_exception(self) -> None:
        canonical = CANONICAL.read_text(encoding="utf-8")
        pm_dispatch = PM_DISPATCH.read_text(encoding="utf-8")
        complete = COMPLETE.read_text(encoding="utf-8")
        review = REVIEW.read_text(encoding="utf-8")

        self.assertIn("sole post-verdict external/UI exception", canonical)
        self.assertIn("After `TERMINAL: COMPLETE` or `TERMINAL: REVISE`", canonical)
        self.assertIn("deliver exactly its\n  authoritative `HUMAN-REQUEST` payload", canonical)
        self.assertIn("same atomic routing unit", canonical)

        self.assertIn("After `COMPLETE` or `REVISE`", pm_dispatch)
        self.assertIn("artifact's `HUMAN-REQUEST` payload", pm_dispatch)
        self.assertIn("transition/send read-back receipts", pm_dispatch)

        self.assertIn(
            "After COMPLETE, allow only the exact approved PR merge when applicable and the matching atomic\n"
            "  `set done`",
            complete,
        )
        self.assertIn("never add a report, UI/mirror write, or other mutation", complete)
        self.assertIn(
            "After REVISE, do not add a\n  completion/report/status/UI mutation",
            complete,
        )
        self.assertIn(
            "After HUMAN_BLOCKED, allow only its exact reviewer-authorized\n  request plus the same atomic",
            complete,
        )
        self.assertIn("`awaiting_human` transition/read-back receipt", complete)
        self.assertIn("no other post-verdict mutation is authorized", complete)

        self.assertIn("Review-owned publication and PR lifecycle", review)
        self.assertIn("mechanical post-review operations", review)
        self.assertIn("never turn an unreviewed change into a success", review)

    def test_old_absolute_bans_and_extra_ask_payloads_are_retired(self) -> None:
        combined = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (CANONICAL, PM_DISPATCH, COMPLETE, REVIEW)
        )
        for retired in (
            "no UI write may follow the verdict",
            "Never allow post-terminal deliverable, memory, report, self-evolution, or UI mutations",
            "post \"still waiting\"",
            "appends the board pointer",
            "the recommended option when applicable",
        ):
            with self.subTest(retired=retired):
                self.assertNotIn(retired, combined)


if __name__ == "__main__":
    unittest.main()
