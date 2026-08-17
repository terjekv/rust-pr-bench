import json
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock

from testlib import load_script_module


postgres_smoke = load_script_module(
    "postgres_select_smoke", "tests/fixtures/postgres_select_smoke.py"
)


class PostgresSelectSmokeTests(unittest.TestCase):
    def test_query_requires_select_one_result(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="1\n", stderr=""
        )
        with mock.patch.object(
            postgres_smoke.subprocess, "run", return_value=completed
        ) as run, mock.patch.object(
            postgres_smoke.time, "perf_counter_ns", side_effect=(100, 250)
        ):
            self.assertEqual(postgres_smoke.query_postgres("postgres-fixture"), 150)

        command = run.call_args.args[0]
        self.assertEqual(command[:3], ["docker", "exec", "postgres-fixture"])
        self.assertEqual(command[-2:], ["--command", "SELECT 1"])

    def test_query_rejects_an_unexpected_result(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="2\n", stderr=""
        )
        with mock.patch.object(
            postgres_smoke.subprocess, "run", return_value=completed
        ):
            with self.assertRaisesRegex(RuntimeError, "expected '1'"):
                postgres_smoke.query_postgres("postgres-fixture")

    def test_writes_criterion_compatible_estimate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = pathlib.Path(tmp)
            postgres_smoke.write_criterion_result(target, 1234)

            output = (
                target
                / "criterion"
                / "postgres-select"
                / "new"
                / "estimates.json"
            )
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["mean"]["point_estimate"], 1234.0)
            self.assertEqual(payload["median"]["point_estimate"], 1234.0)


if __name__ == "__main__":
    unittest.main()
