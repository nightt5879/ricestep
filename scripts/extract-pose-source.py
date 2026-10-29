"""Extract a pinned source archive into a new contained staging directory.

No archive APIs that implicitly extract paths/links are used. Regular files are
written first; safe relative symlinks are created last and never traversed.
"""
import argparse
import os
import pathlib
import posixpath
import shutil
import stat
import tarfile
import zipfile

MAX_FILES = 20000
MAX_BYTES = 512 * 1024 * 1024


def parts(name):
    if not isinstance(name, str) or not name or "\\" in name or "\0" in name or ":" in name or name.startswith("/"):
        raise ValueError(f"Unsafe archive path: {name!r}")
    components = name.rstrip("/").split("/")
    if any(p in ("", ".", "..") or p.endswith((".", " ")) for p in components):
        raise ValueError(f"Unsafe archive path: {name!r}")
    # Windows device names remain special even with a filename extension.
    reserved = {"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)}
    if any(p.split(".", 1)[0].lower() in reserved for p in components):
        raise ValueError(f"Reserved device path: {name!r}")
    return components


def extract(archive, prefix, output):
    if len(parts(prefix)) != 1:
        raise ValueError("Expected a single archive root prefix")
    output = pathlib.Path(output)
    if output.exists() or output.is_symlink():
        raise ValueError("Staging destination must not exist")
    # Parent is a private staging directory created by the caller, not a link.
    for ancestor in (output.parent, *output.parent.parents):
        if ancestor.is_symlink():
            raise ValueError("Staging parent cannot traverse a symlink")

    is_zip = str(archive).endswith(".zip")
    source = zipfile.ZipFile(archive) if is_zip else tarfile.open(archive, "r:gz")
    with source:
        entries = []
        total = 0
        for member in source.infolist() if is_zip else source.getmembers():
            # ZipInfo.filename normalizes Windows separators and truncates NUL;
            # validate the original archive name before that normalization.
            name = member.orig_filename if is_zip else member.name
            components = parts(name)
            if components[0] != prefix:
                raise ValueError(f"Entry outside pinned root: {name}")
            relative = "/".join(components[1:])
            if is_zip:
                mode = (member.external_attr >> 16) & 0o170000
                if member.is_dir():
                    kind, size, link = "directory", 0, None
                elif mode in (0, stat.S_IFREG):
                    kind, size, link = "file", member.file_size, None
                else:
                    raise ValueError(f"Unsupported ZIP entry type: {name}")
            elif member.isdir():
                kind, size, link = "directory", 0, None
            elif member.isfile():
                kind, size, link = "file", member.size, None
            elif member.issym():
                kind, size, link = "symlink", 0, member.linkname
            else:
                raise ValueError(f"Unsupported TAR entry type: {name}")
            if not relative:
                if kind != "directory":
                    raise ValueError("Archive root must be a directory")
                continue
            total += size
            entries.append((relative, kind, size, link, member))
            if len(entries) > MAX_FILES or total > MAX_BYTES or size < 0:
                raise ValueError("Source archive exceeds extraction limits")

        # Treat case-fold aliases as duplicates on every host, for Windows portability.
        indexed, aliases = {}, set()
        for entry in entries:
            name = entry[0]
            if name.casefold() in aliases:
                raise ValueError(f"Duplicate/case-colliding archive path: {name}")
            aliases.add(name.casefold())
            indexed[name] = entry
        for name, kind, _, _, _ in entries:
            for parent in pathlib.PurePosixPath(name).parents:
                ancestor = str(parent)
                if ancestor in indexed and indexed[ancestor][1] != "directory":
                    raise ValueError(f"Entry traverses non-directory/link: {name}")

        def link_target(name, link):
            if not link or "\\" in link or ":" in link or "\0" in link or link.startswith("/"):
                raise ValueError(f"Unsafe symlink: {name}")
            target = posixpath.normpath(posixpath.join(posixpath.dirname(name), link))
            if target == ".." or target.startswith("../") or target.startswith("/"):
                raise ValueError(f"Symlink escapes source root: {name}")
            parts(target)
            return target

        targets = {name: link_target(name, link) for name, kind, _, link, _ in entries if kind == "symlink"}
        def resolved_target(name):
            seen = {name}
            target = targets[name]
            while target in targets:
                if target in seen:
                    raise ValueError(f"Cyclic symlink: {name}")
                seen.add(target)
                target = targets[target]
            if target not in indexed:
                raise ValueError(f"Dangling archive symlink: {name}")
            # A target path may not traverse a link in one of its parent components.
            for parent in pathlib.PurePosixPath(target).parents:
                if str(parent) in targets:
                    raise ValueError(f"Symlink target traverses another link: {name}")
            return target
        for name in targets:
            resolved_target(name)

        if not entries:
            raise ValueError("Empty source archive")
        output.mkdir()
        for name, kind, size, _, member in entries:
            destination = output.joinpath(*name.split("/"))
            if kind == "directory":
                destination.mkdir(parents=True, exist_ok=True)
            elif kind == "file":
                destination.parent.mkdir(parents=True, exist_ok=True)
                with (source.open(member) if is_zip else source.extractfile(member)) as reader, destination.open("xb") as writer:
                    shutil.copyfileobj(reader, writer)
                if destination.stat().st_size != size:
                    raise ValueError(f"Unexpected extracted length: {name}")
        for name, kind, _, link, _ in entries:
            if kind == "symlink":
                destination = output.joinpath(*name.split("/"))
                destination.parent.mkdir(parents=True, exist_ok=True)
                target = resolved_target(name)
                # Windows reparse targets require native separators to remain
                # readable; inventory normalizes them back to archive POSIX form.
                os.symlink(os.path.normpath(link), destination, target_is_directory=indexed[target][1] == "directory")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("archive")
    parser.add_argument("prefix")
    parser.add_argument("output")
    args = parser.parse_args()
    extract(args.archive, args.prefix, args.output)
