"""Tests about the test suite itself.

A suite that quietly runs fewer tests than it appears to is worse than a
smaller suite, because the number on screen keeps saying everything is fine.
"""
import ast
import os
import subprocess
import tempfile
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import support  # noqa: F401  removes this run's temp directories


def _test_files():
    return sorted(name for name in os.listdir(HERE)
                  if name.startswith("test_") and name.endswith(".py"))


NESTED = "PLUGIN_INVENTAR_NESTED_TESTRUN"


def _child_environment(**extra):
    """Environment for a nested suite run, marked so it does not nest again."""
    environment = dict(os.environ, **extra)
    environment[NESTED] = "1"
    return environment


class GuardIsLast(unittest.TestCase):
    def test_nothing_is_defined_after_the_main_guard(self):
        """`if __name__ == "__main__"` has to be the last thing in a file.

        Six tests were appended below the guard once and vanished from the
        direct run without a word -- while it still reported OK. In a tool
        whose whole promise is "nothing disappears silently", that is the
        one mistake the test suite must not make.
        """
        for name in _test_files():
            with open(os.path.join(HERE, name), encoding="utf-8") as handle:
                tree = ast.parse(handle.read(), filename=name)
            for index, node in enumerate(tree.body):
                is_guard = (isinstance(node, ast.If)
                            and ast.dump(node.test).find("__main__") != -1)
                if is_guard:
                    after = tree.body[index + 1:]
                    self.assertEqual(
                        [], after,
                        f"{name}: {len(after)} definitions sit behind the __main__ "
                        f"guard and are skipped on a direct run")

    @unittest.skipIf(os.environ.get(NESTED), "already inside a nested run")
    def test_every_file_runs_standalone(self):
        """Each file has to pass on its own, not only inside discover.

        Otherwise one file silently depends on another having run first.
        """
        for name in _test_files():
            if name == os.path.basename(__file__):
                continue
            result = subprocess.run(
                [sys.executable, "-m", "unittest", name[:-3]],
                cwd=HERE, capture_output=True, text=True,
                env=_child_environment(), timeout=120)
            self.assertEqual(0, result.returncode,
                             f"{name} on its own: {result.stderr[-500:]}")



class NothingLeaksIntoTheUsersHome(unittest.TestCase):
    @unittest.skipIf(os.environ.get(NESTED), "already inside a nested run")
    def test_no_test_writes_into_the_real_state_directory(self):
        """One setUp set XDG_STATE_HOME and never reset it.

        It stayed put for the rest of the process. Harmless only as long as
        that file happened to run last -- and 46 state files with fixture
        names in the user's own ~/.local/state showed it had already gone
        wrong once. This runs the whole suite with HOME pointing at a
        throwaway directory and no XDG_STATE_HOME at all.
        """
        import shutil
        home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        environment = {k: v for k, v in os.environ.items()
                       if k != "XDG_STATE_HOME"}
        environment["HOME"] = home
        environment[NESTED] = "1"
        result = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", HERE],
            cwd=os.path.dirname(HERE), env=environment,
            capture_output=True, text=True, timeout=120)
        self.assertEqual(0, result.returncode, result.stderr[-800:])
        leaked = os.path.join(home, ".local", "state")
        self.assertFalse(os.path.exists(leaked),
                         f"suite wrote into {leaked}")



class TheSuiteCleansUpAfterItself(unittest.TestCase):
    @unittest.skipIf(os.environ.get(NESTED), "already inside a nested run")
    def test_no_temporary_directories_are_left_behind(self):
        """A full run used to leave 184 directories and 2 MB in $TMPDIR.

        Only one of the test files cleaned up after itself, and test_meta
        starts the run twice more as a subprocess.
        """
        import shutil
        scratch = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, scratch, ignore_errors=True)
        result = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", HERE],
            cwd=os.path.dirname(HERE), env=_child_environment(TMPDIR=scratch),
            capture_output=True, text=True, timeout=120)
        self.assertEqual(0, result.returncode, result.stderr[-500:])
        self.assertEqual([], os.listdir(scratch))


if __name__ == "__main__":
    unittest.main()
