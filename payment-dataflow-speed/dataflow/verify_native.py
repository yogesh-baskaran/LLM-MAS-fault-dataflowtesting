import argparse
import json
from pathlib import Path
from dataflow.common import sha256


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,required=True);parser.add_argument('--repo',type=Path,required=True)
    args=parser.parse_args();path=args.root/'runtime/native/source-manifest.json'
    if not path.exists():raise RuntimeError('Native builds missing. Run bash speed/setup-multi.sh inside your allocation.')
    manifest=json.loads(path.read_text());changed=[name for name,value in manifest.items() if not (args.repo/name).is_file() or sha256(args.repo/name)!=value]
    if changed:raise RuntimeError('Native source changed; rerun setup-multi.sh: '+', '.join(changed))


if __name__=='__main__':main()
