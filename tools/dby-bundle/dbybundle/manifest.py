# SPDX-License-Identifier: GPL-2.0-only
"""Schema 1 metadata, deterministic serialization and complete bundle checks."""

from datetime import datetime, timezone
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile

BOOT_FILES = ("Image", "dby-w09.dtb", "initrd.img", "dby-device.img", "MANIFEST")
BOOT_PREFIX = "p1/UBOOT/"
RELEASE = "7.2.10"
ALLOWED_FILES = {BOOT_PREFIX + name for name in BOOT_FILES} | {"rootfs.tar.zst", "rootfs-test.tar.zst"}
HEX = re.compile(r"[0-9a-f]{64}\Z")
TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,127}\Z")
FIELDS = {"schema", "created", "bundle_id", "sources", "kernel_release", "files", "rootfs"}
MAX_METADATA = 1024 * 1024


class BundleError(ValueError):
    """A stable error code and a path-free English explanation."""

    def __init__(self, code, message):
        self.code = code
        super().__init__(f"{code}: {message}")


def require(condition, code, message):
    if not condition:
        raise BundleError(code, message)


def io_errors(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (OSError, UnicodeError, RecursionError):
            raise BundleError("E_BUNDLE_IO", "Cannot read or write bundle data.") from None
    return wrapped


def valid_digest(value):
    return isinstance(value, str) and HEX.fullmatch(value) is not None


def relative_path(name):
    require(isinstance(name, str) and name and not name.startswith("/")
            and "\\" not in name and ":" not in name
            and all(part not in ("", ".", "..") for part in name.split("/"))
            and not any(ord(char) < 32 or ord(char) == 127 for char in name),
            "E_BUNDLE_PATH", "File paths must be canonical relative paths.")
    return name


def regular_open(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        require(stat.S_ISREG(os.fstat(fd).st_mode), "E_BUNDLE_TYPE", "Expected a regular file.")
        return os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise


def file_record(path):
    digest = hashlib.sha256()
    size = 0
    with regular_open(path) as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return {"size": size, "sha256": digest.hexdigest()}


def inventory(directory):
    root = Path(os.path.abspath(directory))
    for parent in (root, *root.parents):
        require(not parent.is_symlink(), "E_BUNDLE_SYMLINK", "Symbolic links are forbidden.")
    require(root.is_dir(), "E_BUNDLE_DIR", "Bundle directory is missing.")
    files = set()

    def visit(folder):
        with os.scandir(folder) as entries:
            for entry in entries:
                name = relative_path(Path(entry.path).relative_to(root).as_posix())
                mode = entry.stat(follow_symlinks=False).st_mode
                require(not stat.S_ISLNK(mode), "E_BUNDLE_SYMLINK", "Symbolic links are forbidden.")
                if stat.S_ISDIR(mode):
                    require(name in ("p1", "p1/UBOOT"), "E_BUNDLE_LAYOUT", "Unexpected bundle directory.")
                    visit(entry.path)
                else:
                    require(stat.S_ISREG(mode), "E_BUNDLE_TYPE", "Only regular files are allowed.")
                    if name != "manifest.json":
                        files.add(name)
    visit(root)
    return root, files


def redact_sources(sources):
    """Copy only the schema's provenance fields, retaining private digests only."""
    require(isinstance(sources, dict) and not sources.keys() - {
        "repositories", "upstream", "inputs", "private_inputs"},
        "E_BUNDLE_METADATA", "Unsupported source metadata.")
    result = {}
    if "repositories" in sources:
        repos = sources["repositories"]
        require(isinstance(repos, dict) and not repos.keys() - {"linux", "kernel", "uboot"},
                "E_BUNDLE_METADATA", "Unsupported repository metadata.")
        require(all(isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", sha)
                    for sha in repos.values()), "E_BUNDLE_METADATA", "Invalid repository commit.")
        result["repositories"] = dict(repos)
    if "upstream" in sources:
        upstream = sources["upstream"]
        require(isinstance(upstream, list), "E_BUNDLE_METADATA", "Invalid upstream records.")
        for item in upstream:
            require(isinstance(item, dict) and item.keys() == {"name", "version", "sha256"}
                    and all(isinstance(item[key], str) and TOKEN.fullmatch(item[key])
                            for key in ("name", "version")) and valid_digest(item["sha256"]),
                    "E_BUNDLE_METADATA", "Invalid upstream record.")
        result["upstream"] = sorted((dict(item) for item in upstream),
                                    key=lambda item: (item["name"], item["version"], item["sha256"]))
    if "inputs" in sources:
        inputs = sources["inputs"]
        require(isinstance(inputs, dict) and all(isinstance(key, str) and TOKEN.fullmatch(key)
                and valid_digest(value) for key, value in inputs.items()),
                "E_BUNDLE_METADATA", "Inputs must use role names and digests only.")
        result["inputs"] = dict(inputs)
    if "private_inputs" in sources:
        private = sources["private_inputs"]
        require(isinstance(private, list) and all(isinstance(item, dict) and valid_digest(item.get("sha256"))
                                                for item in private),
                "E_BUNDLE_METADATA", "Private inputs must provide SHA-256 digests.")
        result["private_inputs"] = sorted(({"sha256": item["sha256"]} for item in private),
                                          key=lambda item: item["sha256"])
    return result


def validate_metadata(document):
    require(isinstance(document, dict), "E_BUNDLE_JSON", "Manifest must be a JSON object.")
    require(type(document.get("schema")) is int and document["schema"] == 1,
            "E_BUNDLE_SCHEMA", "Unsupported manifest schema.")
    require(document.keys() == FIELDS, "E_BUNDLE_METADATA", "Manifest fields do not match schema 1.")
    created = document["created"]
    require(isinstance(created, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", created),
            "E_BUNDLE_METADATA", "Creation time must be UTC with second precision.")
    try:
        stamp = datetime.strptime(created, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        raise BundleError("E_BUNDLE_METADATA", "Invalid UTC creation time.") from None
    identifier = document["bundle_id"]
    require(isinstance(identifier, str) and re.fullmatch(stamp.strftime("%Y%m%d") + r"-[1-9][0-9]*", identifier),
            "E_BUNDLE_METADATA", "Bundle ID must match its creation date and positive sequence.")
    require(document["kernel_release"] == RELEASE, "E_BUNDLE_RELEASE", "Unsupported kernel release.")
    require(redact_sources(document["sources"]) == document["sources"],
            "E_BUNDLE_METADATA", "Source metadata must be canonical and contain no private paths.")
    rootfs = document["rootfs"]
    require(isinstance(rootfs, dict), "E_BUNDLE_METADATA", "Invalid rootfs metadata.")
    if rootfs:
        require(rootfs.keys() == {"recipe_sha256", "packages_sha256", "package_count"}
                and valid_digest(rootfs["recipe_sha256"]) and valid_digest(rootfs["packages_sha256"])
                and type(rootfs["package_count"]) is int and rootfs["package_count"] >= 0,
                "E_BUNDLE_METADATA", "Invalid rootfs recipe or package summary.")
    files = document["files"]
    require(isinstance(files, dict), "E_BUNDLE_METADATA", "Invalid file inventory.")
    for name, record in files.items():
        relative_path(name)
        require(isinstance(record, dict) and record.keys() == {"size", "sha256"}
                and type(record["size"]) is int and record["size"] >= 0 and valid_digest(record["sha256"]),
                "E_BUNDLE_METADATA", "Invalid file size or SHA-256 record.")
    require({BOOT_PREFIX + name for name in BOOT_FILES} <= files.keys()
            and not files.keys() - ALLOWED_FILES, "E_BUNDLE_LAYOUT", "Invalid bundle file layout.")
    require(not rootfs or "rootfs.tar.zst" in files,
            "E_BUNDLE_LAYOUT", "Rootfs metadata requires the full rootfs archive.")


def check_boot_manifest(root, records):
    with regular_open(root / BOOT_PREFIX / "MANIFEST") as stream:
        data = stream.read(1024)
    expected = "".join(f"{records[BOOT_PREFIX + name]['sha256']}  {name}\n" for name in BOOT_FILES[:3]).encode("ascii")
    require(data == expected, "E_BUNDLE_BOOT_MANIFEST", "Boot MANIFEST hashes or order do not match.")


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "E_BUNDLE_JSON", "Duplicate JSON keys are forbidden.")
        result[key] = value
    return result


def invalid_constant(value):
    raise BundleError("E_BUNDLE_JSON", "Nonfinite JSON numbers are forbidden.")


@io_errors
def read_manifest(directory):
    """Read schema metadata without hashing payloads; use verify for trust."""
    root, _ = inventory(directory)
    require((root / "manifest.json").exists(), "E_BUNDLE_MISSING", "Manifest is missing.")
    with regular_open(root / "manifest.json") as stream:
        data = stream.read(MAX_METADATA + 1)
    require(len(data) <= MAX_METADATA, "E_BUNDLE_JSON", "Manifest exceeds the metadata size limit.")
    try:
        document = json.loads(data, object_pairs_hook=unique_object, parse_constant=invalid_constant)
    except (ValueError, UnicodeError) as error:
        if isinstance(error, BundleError):
            raise
        raise BundleError("E_BUNDLE_JSON", "Cannot parse manifest JSON.") from None
    validate_metadata(document)
    return document


def encode_manifest(document):
    """Return canonical UTF-8 JSON bytes."""
    validate_metadata(document)
    data = (json.dumps(document, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False) + "\n").encode("utf-8")
    require(len(data) <= MAX_METADATA, "E_BUNDLE_JSON", "Manifest exceeds the metadata size limit.")
    return data


@io_errors
def create_manifest(directory, *, sequence, sources, created=None, rootfs=None):
    """Describe a complete or p1-only bundle without writing any files."""
    require(type(sequence) is int and sequence >= 1, "E_BUNDLE_METADATA", "Sequence must be a positive integer.")
    created = created if created is not None else datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    require(isinstance(created, str), "E_BUNDLE_METADATA", "Invalid creation time.")
    require(rootfs is None or isinstance(rootfs, dict), "E_BUNDLE_METADATA", "Invalid rootfs metadata.")
    root, names = inventory(directory)
    require(not names - ALLOWED_FILES, "E_BUNDLE_EXTRA", "Bundle contains unlisted files.")
    document = {"schema": 1, "created": created, "bundle_id": created[:10].replace("-", "") + f"-{sequence}",
                "sources": redact_sources(sources), "kernel_release": RELEASE,
                "files": {name: file_record(root / name) for name in sorted(names)},
                "rootfs": {} if rootfs is None else dict(rootfs)}
    validate_metadata(document)
    check_boot_manifest(root, document["files"])
    return document


@io_errors
def verify(directory):
    """Check schema, exact inventory, payloads and the ordered boot contract."""
    document = read_manifest(directory)
    root, names = inventory(directory)
    records = document["files"]
    require(not records.keys() - names, "E_BUNDLE_MISSING", "Bundle payload is missing.")
    require(not names - records.keys(), "E_BUNDLE_EXTRA", "Bundle contains unlisted files.")
    for name, expected in records.items():
        actual = file_record(root / name)
        require(actual["size"] == expected["size"], "E_BUNDLE_SIZE", "Payload size does not match.")
        require(actual["sha256"] == expected["sha256"], "E_BUNDLE_HASH", "Payload SHA-256 does not match.")
    check_boot_manifest(root, records)
    return document


@io_errors
def write_manifest(directory, document):
    """Publish canonical metadata without replacing an existing manifest."""
    data = encode_manifest(document)
    root, names = inventory(directory)
    require(not (root / "manifest.json").exists(), "E_BUNDLE_EXISTS", "Manifest already exists.")
    require(names == document["files"].keys(), "E_BUNDLE_LAYOUT", "Payload inventory does not match.")
    for name, expected in document["files"].items():
        require(file_record(root / name) == expected, "E_BUNDLE_HASH", "Payload changed before publication.")
    check_boot_manifest(root, document["files"])
    fd, temporary = tempfile.mkstemp(prefix=".manifest-", dir=root)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, root / "manifest.json")
    finally:
        os.unlink(temporary)
