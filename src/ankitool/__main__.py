import sys

from ankitool.cli.main import main

if __name__ == "__main__":
    sys.argv[0] = "ankitool"          # argparse prog name (otherwise "__main__.py")
    main()
