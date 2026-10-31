"""Generic selectors and profiles do not encode consuming-repository policy."""

import copy
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from benchmark_selection import select, target_id
from expand_matrix import make_build_matrix, make_matrix


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.specs = [
            {"name": "geometry_criterion", "bench": "geometry_criterion", "backend": "criterion", "tags": ["triangles"]},
            {"name": "simulation_callgrind", "bench": "simulation_callgrind", "backend": "gungraun", "tags": ["particles"]},
        ]
        self.config = {"version": 1, "profiles": {
            "interactive": {"criterion_args": "--noplot --sample-size 10"},
            "laboratory": {"criterion_args": "--noplot --sample-size 100"},
        }}

    def test_existing_callers_select_everything_without_changing_commands(self):
        selected, _, evidence = select(self.specs)
        features = [{"name": "default", "features": ""}]
        original = make_matrix(self.specs, features, "--locked", "--noplot")
        actual = make_matrix(selected, features, "--locked", "--noplot")
        self.assertEqual([x["command"] for x in actual["include"]], [x["command"] for x in original["include"]])
        self.assertTrue(all(item["selected"] for item in evidence["targets"]))

    def test_tags_and_explicit_targets_are_unioned_before_building(self):
        selected, catalog, _ = select(self.specs, tags=["triangles"])
        features = [{"name": "default", "features": ""}]
        builds = make_build_matrix(make_matrix(selected, features, "", ""),
                                   catalog=make_matrix(catalog, features, "", ""))
        self.assertTrue(all(len(item["cases"]) == 1 and len(item["cache_cases"]) == 2
                            for item in builds["include"]))
        selected, _, _ = select(self.specs, tags=["triangles"], targets=[target_id(self.specs[1])])
        self.assertEqual(len(selected), 2)

    def test_runtime_profiles_keep_executable_identity_but_separate_history(self):
        features = [{"name": "default", "features": ""}]
        matrices = []
        for name in self.config["profiles"]:
            specs, _, _ = select(self.specs, self.config, profile=name)
            matrices.append(make_matrix(specs, features, "--locked", "--noplot"))
        self.assertNotEqual(matrices[0]["include"][0]["feature_name"], matrices[1]["include"][0]["feature_name"])
        self.assertEqual(make_build_matrix(matrices[0])["include"][0]["cases"],
                         make_build_matrix(matrices[1])["include"][0]["cases"])

    def test_moved_base_receives_the_same_profile(self):
        spec = copy.deepcopy(self.specs[0])
        spec.update(moved=True, base={"name": "old", "bench": "old", "backend": "criterion"})
        selected, _, _ = select([spec], self.config, profile="laboratory")
        self.assertEqual(selected[0]["criterion_args"], selected[0]["base"]["criterion_args"])

    def test_metadata_does_not_create_a_second_target_inventory(self):
        config = {"targets": {target_id(self.specs[0]): {"tags": ["rendering"]}}}
        selected, _, _ = select(self.specs, config, tags=["rendering"])
        self.assertEqual(len(selected), 1)

    def test_unknown_requests_fail_clearly(self):
        for request in ({"tags": ["missing"]}, {"targets": ["missing"]}, {"profile": "missing"}):
            with self.subTest(request=request), self.assertRaisesRegex(ValueError, "Unknown"):
                select(self.specs, self.config, **request)

    def test_existing_named_cases_can_share_one_executable(self):
        other = {**self.specs[0], "name": "another-named-case"}
        selected, _, _ = select([self.specs[0], other], targets=[target_id(other)])
        self.assertEqual(len(selected), 2)

    def test_profiles_cannot_change_build_settings(self):
        with self.assertRaisesRegex(ValueError, "Unknown"):
            select(self.specs, {"profiles": {"bad": {"cargo_args": "--features something"}}})


if __name__ == "__main__":
    unittest.main()
