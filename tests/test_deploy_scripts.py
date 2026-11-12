from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PrepareSnapshotTest(unittest.TestCase):
    def test_no_upload_writes_immutable_release_binding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "weibo.db"
            snapshot_dir = root / "dist"
            conn = sqlite3.connect(db_path)
            try:
                conn.execute("CREATE TABLE posts (id INTEGER PRIMARY KEY)")
                conn.execute("CREATE TABLE post_categories (post_id INTEGER)")
                conn.execute("INSERT INTO posts DEFAULT VALUES")
            finally:
                conn.close()

            env = os.environ.copy()
            env.update(
                {
                    "DB_PATH": str(db_path),
                    "SNAPSHOT_DIR": str(snapshot_dir),
                }
            )
            subprocess.run(
                [str(ROOT / "scripts" / "prepare-snapshot.sh"), "--no-upload"],
                cwd=ROOT,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )

            binding = dict(
                line.split("=", 1)
                for line in (snapshot_dir / "latest.env").read_text(encoding="utf-8").splitlines()
            )
            self.assertRegex(binding["RELEASE_TAG"], r"^data-\d{8}T\d{6}Z$")
            self.assertRegex(binding["SNAPSHOT_ASSET"], r"^weibo-\d{8}T\d{6}Z\.db\.gz$")
            self.assertRegex(binding["SNAPSHOT_SHA256"], r"^[0-9a-f]{64}$")


class DeployScriptTest(unittest.TestCase):
    def test_passes_release_tag_to_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copy2(ROOT / "deploy.sh", root / "deploy.sh")
            dist = root / "dist"
            dist.mkdir()
            (dist / "latest.env").write_text(
                "RELEASE_TAG=data-20261112T083241Z\n"
                "SNAPSHOT_ASSET=weibo-20261112T083241Z.db.gz\n"
                f"SNAPSHOT_SHA256={'a' * 64}\n",
                encoding="utf-8",
            )

            fake_bin = root / "bin"
            fake_bin.mkdir()
            gh_args = root / "gh-args.txt"
            gh = fake_bin / "gh"
            gh.write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                'if [[ "${1:-} ${2:-}" == "auth status" ]]; then exit 0; fi\n'
                'printf "%s\\n" "$@" > "$GH_ARGS"\n',
                encoding="utf-8",
            )
            gh.chmod(0o755)

            env = os.environ.copy()
            env.update(
                {
                    "DEPLOY_BRANCH": "main",
                    "GH_ARGS": str(gh_args),
                    "PATH": f"{fake_bin}:{env['PATH']}",
                }
            )
            subprocess.run(
                ["bash", str(root / "deploy.sh")],
                cwd=root,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )

            args = gh_args.read_text(encoding="utf-8").splitlines()
            self.assertIn("release_tag=data-20261112T083241Z", args)


if __name__ == "__main__":
    unittest.main()
