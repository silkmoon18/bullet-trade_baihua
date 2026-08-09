"""Create a reproducible local interpreter for JoinQuant strategy editing."""

from __future__ import print_function

import argparse
import os
import subprocess
import venv


def _venv_python(venv_dir):
    if os.name == "nt":
        return os.path.join(venv_dir, "Scripts", "python.exe")
    return os.path.join(venv_dir, "bin", "python")


def _normalise_path(path):
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def _is_within(path, parent):
    normalised_path = _normalise_path(path)
    normalised_parent = _normalise_path(parent)
    try:
        return os.path.commonpath([normalised_path, normalised_parent]) == normalised_parent
    except ValueError:
        return False


def _validate_venv_metadata(venv_dir, prefix, base_prefix):
    if _normalise_path(prefix) == _normalise_path(base_prefix):
        raise RuntimeError("target interpreter is not a virtual environment")
    if _normalise_path(prefix) != _normalise_path(venv_dir):
        raise RuntimeError(
            "target interpreter prefix does not match --venv: {}".format(prefix)
        )


def _validate_venv(python, venv_dir):
    metadata = subprocess.check_output(
        [python, "-c", "import sys; print(sys.prefix); print(sys.base_prefix)"],
        text=True,
    ).splitlines()
    if len(metadata) != 2:
        raise RuntimeError("unable to verify target virtual environment")
    _validate_venv_metadata(venv_dir, metadata[0], metadata[1])


def _install_source_paths(python, repo, venv_dir):
    site_packages = subprocess.check_output(
        [
            python,
            "-c",
            "import sysconfig; print(sysconfig.get_path('purelib'))",
        ],
        text=True,
    ).strip()
    if not _is_within(site_packages, venv_dir):
        raise RuntimeError(
            "refusing to write outside target virtual environment: {}".format(
                site_packages
            )
        )
    path_file = os.path.join(site_packages, "bullet_trade_joinquant_dev.pth")
    with open(path_file, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(repo + "\n")
        stream.write(os.path.join(repo, "helpers") + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--venv", default=".venv")
    parser.add_argument(
        "--full",
        action="store_true",
        help="also install BulletTrade base runtime and repository dev dependencies",
    )
    parser.add_argument("--skip-check", action="store_true")
    args = parser.parse_args()

    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
    venv_dir = os.path.abspath(os.path.join(repo, args.venv))
    if _normalise_path(venv_dir) == _normalise_path(repo):
        raise RuntimeError("--venv must not be the repository root")
    python = _venv_python(venv_dir)
    if not os.path.exists(python):
        if os.path.isdir(venv_dir) and os.listdir(venv_dir):
            raise RuntimeError(
                "refusing to initialise a non-empty non-venv directory: {}".format(
                    venv_dir
                )
            )
        venv.EnvBuilder(with_pip=True).create(venv_dir)
    _validate_venv(python, venv_dir)
    subprocess.check_call(
        [python, "-m", "pip", "install", "pip>=21.3"],
        cwd=repo,
    )

    if args.full:
        install_command = [
            python,
            "-m",
            "pip",
            "install",
            "-e",
            ".[joinquant-dev,dev]",
        ]
        subprocess.check_call(install_command, cwd=repo)
    else:
        subprocess.check_call(
            [python, "-m", "pip", "install", "--no-deps", "-e", "."],
            cwd=repo,
        )
        subprocess.check_call(
            [
                python,
                "-m",
                "pip",
                "install",
                "--no-binary=mypy",
                "mypy>=1.8,<1.12",
                "pyright>=1.1.390,<2",
            ],
            cwd=repo,
        )

    _install_source_paths(python, repo, venv_dir)
    if not args.skip_check:
        subprocess.check_call(
            [python, "-m", "mypy", "--config-file", "mypy.joinquant.ini"],
            cwd=repo,
        )
        subprocess.check_call(
            [python, "-m", "pyright", "-p", "pyrightconfig.joinquant.json"],
            cwd=repo,
        )

    print("JoinQuant development interpreter: {}".format(python))
    print("PyCharm: Settings > Python Interpreter > Add Existing > choose the path above")


if __name__ == "__main__":
    main()
