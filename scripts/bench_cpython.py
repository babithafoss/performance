#!/usr/bin/env python3
import argparse
import datetime
import logging
import os.path
import shutil
import subprocess
import sys
import time


GET_PIP_URL = 'https://bootstrap.pypa.io/get-pip.py'


class BenchmarkPython(object):
    def __init__(self):
        self.args = self.parse_args()
        self.python = None

        log_format = '%(asctime)-15s: %(message)s'
        logging.basicConfig(format=log_format)
        self.logger = logging.getLogger()
        if self.args.log:
            handler = logging.FileHandler(self.args.log)
            formatter = logging.Formatter(log_format)
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)

    def create_subprocess(self, cmd, **kwargs):
        self.logger.error("+ %s" % ' '.join(cmd))
        return subprocess.Popen(cmd, **kwargs)

    def run_nocheck(self, *cmd):
        proc = self.create_subprocess(cmd,
                                      stdout=subprocess.PIPE,
                                      stderr=subprocess.STDOUT,
                                      universal_newlines=True)
        with proc:
            for line in proc.stdout:
                line = line.rstrip()
                self.logger.error(line)
            exitcode = proc.wait()

        return exitcode

    def run(self, *cmd):
        exitcode = self.run_nocheck(*cmd)
        if exitcode:
            sys.exit(exitcode)

    def get_output(self, *cmd):
        proc = self.create_subprocess(cmd,
                                      stdout=subprocess.PIPE,
                                      universal_newlines=True)
        with proc:
            stdout = proc.communicate()[0]

        exitcode = proc.wait()
        if exitcode:
            self.logger.error(stdout, end='')
            sys.exit(exitcode)

        return stdout

    def prepare_scm(self):
        args = self.args

        if args.pull:
            self.run('hg', 'pull')

        self.logger.error('')
        text = "Benchmark CPython revision %s" % args.revision
        self.logger.error(text)
        self.logger.error("=" * len(text))
        self.logger.error('')

        self.run('hg', 'up', '--clean', '-r', args.revision)

        # FIXME: run hg purge?

        full_revision = self.get_output('hg', 'id', '-i').strip()
        if not full_revision:
            self.logger.error("ERROR: unable to get the Mercurial revision")
            sys.exit(1)
        self.logger.error("Mercurial revision: %s" % full_revision)

    def compile(self):
        args = self.args

        self.run_nocheck('make', 'distclean')

        config_args = []
        if args.debug:
            config_args.append('--with-pydebug')
        elif args.lto:
            config_args.append('--with-lto')
        if args.prefix:
            config_args.extend(('--prefix', args.prefix))
        self.run('./configure', *config_args)

        self.run_nocheck('make', 'clean')

        if args.pgo:
            # FIXME: use taskset (isolated CPUs) for PGO?
            self.run('make', 'profile-opt')
        else:
            self.run('make')

    def rmtree(self, directory):
        if not os.path.exists(directory):
            return

        self.logger.error("Remove directory %s" % directory)
        shutil.rmtree(directory)

    def install(self):
        args = self.args
        prefix = args.prefix

        if sys.platform in ('darwin', 'win32'):
            program_ext = '.exe'
        else:
            program_ext = ''

        if prefix:
            self.rmtree(prefix)

            self.run('make', 'install')

            self.python = os.path.join(prefix, "bin", "python" + program_ext)
            if not os.path.exists(self.python):
                self.python = os.path.join(prefix, "bin", "python3" + program_ext)
        else:
            self.python = "./python" + program_ext

        exitcode = self.run_nocheck(self.python, '-m', 'pip', '--version')
        if exitcode:
            # pip is missing (or broken?): install it
            self.run('wget', GET_PIP_URL, '-O', 'get-pip.py')
            self.run(self.python, 'get-pip.py')

        # Install performance
        self.run(self.python, '-m', 'pip', 'install', '-U', 'performance')

    def run_benchmark(self):
        args = self.args

        # Create venv
        cmd = [self.python, '-m', 'performance', 'venv', 'recreate']
        if args.venv:
            cmd.extend(('--venv', args.venv))
        self.run(*cmd)

        # python -u: unbuffered output to see output faster
        cmd = [self.python, '-u',
               '-m', 'performance',
               'run', '-b', 'all,-pybench',
               '-v']
        if args.output:
            cmd.extend(('--output', args.output))
        if args.venv:
            cmd.extend(('--venv', args.venv))
        if args.debug:
            cmd.append('--debug-single-sample')
        elif args.rigorous:
            cmd.append('--rigorous')
        self.run(*cmd)

    def parse_args(self):
        parser = argparse.ArgumentParser()
        parser.add_argument('-o', '--output', metavar='FILENAME',
                            help='write results encoded to JSON into FILENAME')
        parser.add_argument('--log', metavar='FILENAME',
                            help='Write logs into FILENAME log file')
        parser.add_argument('--pgo', action='store_true',
                            help='Enable Profile Guided Optimization (PGO)')
        parser.add_argument('--lto', action='store_true',
                            help='Enable Link Time Optimization (LTO)')
        parser.add_argument('--src',
                            help='Directory of Python source code',
                            required=True)
        parser.add_argument('--venv',
                            help="Directory of virtual environmented used "
                                 "to run performance benchmarks. Create it "
                                 "if it doesn't exist")
        parser.add_argument('--prefix',
                            help="Directory where Python is installed: "
                                 "--prefix parameter of the ./configure script.")
        parser.add_argument('--debug', action="store_true",
                            help="Enable the debug mode")
        parser.add_argument('--rigorous', action="store_true",
                            help="Enable the rigorous mode: "
                                 "run more benchmarks samples")
        parser.add_argument('--pull',
                            help='Run hg pull -u to update the Mercurial '
                                 'repository')
        parser.add_argument('revision',
                            help='Python benchmarked revision')
        args = parser.parse_args()

        if not args.prefix:
            # FIXME
            print("ERROR: running benchmark without installation "
                  "is currently broken")
            sys.exit(1)

        for attr in ('src', 'prefix', 'output'):
            # Since we use os.chdir(), all paths must be absolute
            path = getattr(args, attr)
            if not path:
                continue
            path = os.path.expanduser(path)
            path = os.path.realpath(path)
            setattr(args, attr, path)

        if args.debug:
            args.pgo = False
            args.lto = False

        if args.venv:
            args.venv = os.path.realpath(args.venv)

        if args.output and os.path.exists(args.output):
            print("ERROR: %s already exists" % args.output)
            sys.exit(1)

        return args

    def main(self):
        self.start = time.monotonic()

        self.logger.error("Run benchmarks")
        self.logger.error('')

        if self.args.log:
            self.logger.error("Write logs into %s" % self.args.log)

        self.logger.error("Move to %s" % self.args.src)
        os.chdir(self.args.src)

        self.prepare_scm()
        self.compile()
        self.install()
        self.run_benchmark()

        dt = time.monotonic() - self.start
        dt = datetime.timedelta(seconds=dt)
        self.logger.error("Benchmark completed in %s" % dt)


if __name__ == "__main__":
    BenchmarkPython().main()
