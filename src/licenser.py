#!/usr/bin/env python3

# Source paths are independent and extensible:
# Globs are constraints for specific-files
#  dir/ = all files in dir/
#  dir/ *.py = all files in dir/ matching *.py
#  *.py = *.py in pwd


# Source files, paths and globs passed supplied are resolved
# Prune files, paths and globs passed supplied are resolved
# Pruning:
#  if prune_dir in source_path: skip
#  if prune_file == source_path: skip

import pathlib
import argparse
import copy
import os
import sys
import re


DEBUG = False
FILE_TYPE_TO_EXT_MAP = {
    "c": {'c', 'h'},
    "c++": {'cxx', 'cpp', 'hpp', 'cc', 'h'},
    "python": {'py'},
    "text": {'txt'},
    "shell": {'sh', 'bash', 'zsh'},
    "markdown": {'md'},
}
COMMENT_TO_FILE_TYPE_MAP = {
    "#": {"shell", "python", "text"},
    "//": {"c", "c++"},
}
# Preamble, symbols middle,, closing symbol
FILE_TYPE_TO_ENCLOSING_COMMENT_MAP = {
    "markdown": ("<--", " ", "-->"),
}


def parse_args():
    parser = argparse.ArgumentParser(
        prog=__file__,
        description="Licenser",
    )

    parser.add_argument(
        "-g", "--debug", action="store_true", default=False,
        help="Enable debug mode and print extra info."
    )

    parser.add_argument(
        "-r", "--recurse", action="store_true", default=False,
        help="Enable recursive search in source paths."
    )

    parser.add_argument(
        "-d", "--delete", action="store_true", default=False,
        help="Remove license from source paths. Default behavior is to add license."
    )

    parser.add_argument(
        "-l", "--license-file", type=str, required=True,
        help="License file."
    )

    parser.add_argument(
        "-L", "--list-files", action="store_true", default=False,
        help="List processing files and exits."
    )

    parser.add_argument(
        "-s", "--sources", type=str, nargs="+", required=True,
        help=f"Paths to process (filename, directory, globs)."
    )

    parser.add_argument(
        "-p", "--prune", type=str, nargs="+", default=[],
        help="Paths to skip processing (filename, directory, globs)."
    )

    # parser.add_argument(
    #    "-t", "--file-types", type=str, nargs="+", choices=list(FILE_TYPE_TO_EXT_MAP),
    #     help=f"File types of files to process"
    # )

    return parser.parse_args()


def ensure_iterable(obj):
    def is_iterable(obj):
        return hasattr(obj, '__iter__') and not isinstance(obj, (str, bytes))

    return obj if is_iterable(obj) else [obj]


class PathSet:
    def __init__(self, sources):
        # Sets of pathlib.Path objects
        self.dirs = set()
        self.files = set()
        # Set of strings, globs only apply for selecting paths from nested levels
        self.globs = set()
        # Initialize sets
        self.add(sources)


    def add(self, sources):
        """Add files, dirs, and/or globs into independent sets."""
        for source in ensure_iterable(sources):
            # Also, ensures source is a string
            source = os.path.expandvars(source)
            if '*' in source:
                self.globs.add(source)
            else:
                path = pathlib.Path(source)
                # XXX: How to categorize non-existing sources?
                # There is no standard solution.
                #   * assume only files have a period which is used for extensions
                #   * sources ending in path separator are directories
                if path.is_dir() or (not path.is_file() and ('.' not in source or source[-1] == os.sep)):
                    self.dirs.add(path)
                else:
                    # We do not check for existence or object type because
                    # this function should work for pruning paths that do not exist.
                    self.files.add(path)


    def prune(self, prune_ps):
        prune_from_dirs = set()
        prune_from_files = set()
        prune_from_globs = set()

        # Prune by directories
        for prune_path in prune_ps.dirs:
            for source_path in self.dirs:
                if str(prune_path) in str(source_path):
                    prune_from_dirs.add(source_path)
            for source_path in self.files:
                if str(prune_path) in str(source_path):
                    prune_from_files.add(source_path)

        # Prune by files
        for prune_path in prune_ps.files:
            for source_path in self.files:
                if str(prune_path) == str(source_path):
                    prune_from_files.add(source_path)

        # Prune by globs, one-time requirement
        for prune_glob in prune_ps.globs:
            for source_glob in self.globs:
                if prune_glob == source_glob:
                    prune_from_globs.add(source_glob)

        self.dirs -= prune_from_dirs
        self.files -= prune_from_files
        self.globs -= prune_from_globs


    def traverse(self, prune_ps, *, recurse=False):
        def _traverse(source_ps, prune_ps, *, recurse, depth):
            source_ps.prune(prune_ps)
            yield from source_ps.files

            # Always, traverse at least one level deep
            if not recurse and depth > 0:
                return

            for source_path in source_ps.dirs:
                s = PathSet(source_ps.globs)
                s.add([
                    path
                    for path in source_path.iterdir()
                    if path.is_dir()
                ])
                s.add(PathSet.resolve_globs(source_path, source_ps.globs))
                prune_ps.add(PathSet.resolve_globs(source_path, prune_ps.globs))
                yield from _traverse(s, prune_ps, recurse=recurse, depth=depth + 1)

        source_ps = copy.deepcopy(self)
        prune_ps = copy.deepcopy(prune_ps)

        # Consider pwd if no directories or files specified
        if len(source_ps.files) == 0 and len(source_ps.dirs) == 0:
            source_ps.add('.')
        # Consider all files, if no files or globs specified
        if len(source_ps.files) == 0 and len(source_ps.globs) == 0:
            source_ps.add('*')

        source_ps.resolve()
        source_ps.validate()
        prune_ps.resolve()

        yield from _traverse(source_ps, prune_ps, recurse=recurse, depth=0)


    def validate(self):
        PathSet.validate_paths(self.dirs)
        PathSet.validate_paths(self.files)


    def resolve(self):
        self.dirs = PathSet.resolve_paths(self.dirs)
        self.files = PathSet.resolve_paths(self.files)


    @staticmethod
    def validate_paths(paths):
        for path in ensure_iterable(paths):
            if not os.path.exists(path):
                raise Exception(f"path does not exists: {path}")
            if not os.path.isdir(path) and not os.path.isfile(path):
                raise Exception(f"path does not represents a valid object (file, directory): {path}")


    @staticmethod
    def resolve_paths(paths):
        return set(
            pathlib.Path(path).expanduser().resolve()
            for path in ensure_iterable(paths)
        )


    @staticmethod
    def resolve_globs(path, globs):
        paths = set()
        for glob in globs:
            paths |= set(path.glob(glob))
        return PathSet.resolve_paths(paths)


    def __str__(self):
        return (
            f"dirs: {sorted(self.dirs)}{os.linesep}"
            f"files: {sorted(self.files)}{os.linesep}"
            f"globs: {sorted(self.globs)}{os.linesep}"
        )


def get_file_type_from_filename(file):
    suffix = file.suffix
    if suffix:
        suffix = suffix[1:]
        for file_type, exts in FILE_TYPE_TO_EXT_MAP.items():
            if suffix in exts:
                return file_type
    raise Exception(f"unknown file type for given file ({file})")


def get_comment_symbol_from_text(text):
    for symbol in COMMENT_TO_FILE_TYPE_MAP.keys():
        if re.search(fr"^{symbol}", text):
            return symbol
    raise Exception("unknown comment symbol for given text")


def change_comment_symbol(text, to_symbol):
    from_symbol = get_comment_symbol_from_text(text)
    if from_symbol == to_symbol:
        return text
    if isinstance(from_symbol, str) and isinstance(to_symbol, str):
        text = re.sub(fr"^{from_symbol}", to_symbol, text, 0, re.MULTILINE)
    elif isinstance(from_symbol, str):
        text = re.sub(fr"^{from_symbol}", to_symbol[1], text, 0, re.MULTILINE)
    elif isinstance(to_symbol, str):
        text = re.sub(fr"^{from_symbol[1]}", to_symbol, text, 0, re.MULTILINE)
    else:
        text = re.sub(fr"^{from_symbol[1]}", to_symbol[1], text, 0, re.MULTILINE)
    return text


def get_comment_symbol_from_file_type(file_type):
    if file_type in FILE_TYPE_TO_ENCLOSING_COMMENT_MAP:
        return FILE_TYPE_TO_ENCLOSING_COMMENT_MAP[file_type]
    else:
        for symbol, file_types in COMMENT_TO_FILE_TYPE_MAP.items():
            if file_type in file_types:
                return symbol
    raise Exception(f"unknown symbol for file type ({file_type})")


def get_comment_symbol_from_file(file):
    file_type = get_file_type_from_filename(file)
    return get_comment_symbol_from_file_type(file_type)


def add_license(license, files):
    license_text = license.read_text()
    for i, file in enumerate(files, start=1):
        # print(f"{i}. Licensing: {file}")
        # to_symbol = get_comment_symbol_from_file(file)
        # curr_license_text = change_comment_symbol(license_text, to_symbol)
        # print(curr_license_text)

        # Naive solution: prepend license to file
        file_text = file.read_text()
        if license_text not in file_text:
            file.write_text(license_text + file_text)


def remove_license(license, files):
    license_text = license.read_text()
    for i, file in enumerate(files, start=1):
        print(f"{i}. Unlicensing: {file}")
        # file_text = file.read_text()
        # file.write_text(file_text.replace(license_text, ''))


def main(args):
    global DEBUG
    DEBUG = args.debug

    source_ps = PathSet(args.sources)
    prune_ps = PathSet(args.prune)

    if DEBUG:
        print("Sources:")
        print("args: ", args.sources)
        print(source_ps)
        print()
        print("Prune:")
        print("args: ", args.prune)
        print(prune_ps)
        print()

    files = source_ps.traverse(prune_ps, recurse=args.recurse)
    if args.list_files:
        for i, file in enumerate(sorted(files), start=1):
            print(f"{i}. {file}")
    else:
        license = pathlib.Path(args.license_file)
        if args.delete:
            remove_license(license, files)
        else:
            add_license(license, files)


if __name__ == "__main__":
    main(parse_args())
