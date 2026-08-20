"""Example job script. Runs as an isolated subprocess -- never imported by the scheduler."""
import sys


def main() -> int:
    print("example_job: hello from a scheduled run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
