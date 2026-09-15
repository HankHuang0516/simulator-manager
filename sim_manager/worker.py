"""Internal gate. EOF before registration means exit without launching work."""
import os
import sys


def main():
    fd = int(sys.argv[1])
    try:
        ready = os.read(fd, 1)
    finally:
        os.close(fd)
    if ready != b'1':
        return 125
    os.execvpe(sys.argv[2], sys.argv[2:], os.environ)


if __name__ == '__main__':
    raise SystemExit(main())
