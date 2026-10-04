"""Independent, offline numeric-transform reference using pinned Arelle.

This is not an Arelle document/taxonomy validation run. Source parsing, scalar
arithmetic and statuses belong to this adapter; only registry transformations
are delegated to Arelle. Returned source-derived values must remain external.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
from decimal import Decimal
from pathlib import Path
import platform
import re
import sys

from lxml import etree

ARELLE_VERSION = "2.46.0"
ARELLE_WHEEL_SHA256 = "c1105ea4dcd9e77dab5b4d59552a7ba571f481cc6cb61412bef9e2c4890be96b"
FUNCTION_IXT_SHA256 = "ecf6a1098a9ac072d7100864463b57f85a7742d349cafd86de54b5b5249b66aa"
IX_NAMESPACES = frozenset((
    "http://www.xbrl.org/2013/inlineXBRL",
    "http://www.xbrl.org/2008/inlineXBRL",
))
XSI_NIL = "{http://www.w3.org/2001/XMLSchema-instance}nil"
XML_WS = re.compile(r"[ \t\r\n]+")
DECIMAL_LEXICAL = re.compile(r"[+\-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)\Z")
INTEGER_LEXICAL = re.compile(r"[+\-]?[0-9]+\Z")
MAX_TEXT_CHARACTERS = 100000
MAX_ABSOLUTE_SCALE = 100000
_REGISTRY = None


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _registry():
    global _REGISTRY
    if _REGISTRY is None:
        from arelle import FunctionIxt
        version = importlib.metadata.version("arelle-release")
        if version != ARELLE_VERSION:
            raise RuntimeError("reference requires arelle-release==" + ARELLE_VERSION)
        if _sha(Path(FunctionIxt.__file__)) != FUNCTION_IXT_SHA256:
            raise RuntimeError("pinned Arelle FunctionIxt source digest mismatch")
        _REGISTRY = FunctionIxt.ixtNamespaceFunctions
    return _REGISTRY


def parse_document(xml_bytes: bytes):
    """Strict independent XML parser; never fetch DTDs or resolve entities."""
    if not isinstance(xml_bytes, bytes):
        raise TypeError("source must be bytes")
    parser = etree.XMLParser(resolve_entities=False, no_network=True,
                             load_dtd=False, recover=False, huge_tree=False)
    root = etree.fromstring(xml_bytes, parser=parser)
    if any(isinstance(e, etree._Entity) for e in root.iter()):
        raise ValueError("entity references are outside this reference scope")
    return root.getroottree()


def iter_nonfractions(document):
    """Yield recognized nonFraction elements in independent XML traversal order."""
    root = document.getroot() if hasattr(document, "getroot") else document
    for node in root.iter():
        if isinstance(node.tag, str):
            q = etree.QName(node)
            if q.localname == "nonFraction" and q.namespace in IX_NAMESPACES:
                yield node


def _format_qname(node, lexical: str):
    lexical = XML_WS.sub(" ", lexical).strip(" ")
    parts = lexical.split(":")
    if len(parts) == 1:
        prefix, local = None, parts[0]
    elif len(parts) == 2:
        prefix, local = parts
        if not prefix:
            raise ValueError("empty QName prefix")
        if etree.QName(prefix).namespace is not None:
            raise ValueError("prefix must be NCName")
    else:
        raise ValueError("invalid QName")
    if etree.QName(local).namespace is not None:
        raise ValueError("local name must be NCName")
    namespace = node.nsmap.get(prefix)
    if not namespace:
        raise ValueError("unresolved QName namespace")
    return namespace, local


def _source_text(node):
    # No repository helper and no Arelle model objects are involved. Comments
    # and processing instructions do not contribute text; their tails do.
    pieces = [node.text or ""]
    for child in node:
        if isinstance(child.tag, str):
            q = etree.QName(child)
            if q.namespace not in IX_NAMESPACES or q.localname != "nonFraction":
                raise ValueError("nonFraction has child outside supported structure")
            pieces.append(_source_text(child))
        elif isinstance(child, etree._Entity):
            raise ValueError("unresolved entity")
        pieces.append(child.tail or "")
    return "".join(pieces)


def _canonical_scaled(value: Decimal, scale: int, negative: bool) -> str:
    # Constructing a Decimal tuple does not round under the ambient Decimal
    # precision. Exponent shifting is exact multiplication by ten**scale.
    sign, digits, exponent = value.as_tuple()
    shifted = Decimal((int(bool(sign) ^ negative), digits, exponent + scale))
    if shifted.is_zero():
        return "0"
    text = format(shifted, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def normalize_nonfraction(node, *, include_private: bool = False) -> dict:
    """Normalize one node, returning explicit status and nullable decimal text.

    `include_private=True` adds raw/transformed lexical material. Such results
    are source-bearing and must only be written to external execution storage.
    Canonical numeric values themselves are also source-derived.
    """
    result = {"status": None, "canonical_value": None,
              "format_namespace": None, "format_local": None,
              "reference_scope": "numeric_transform_only"}

    def stop(status, reason=None):
        result["status"] = status
        if reason is not None:
            result["reason"] = reason
        return result

    if not isinstance(node.tag, str):
        return stop("not_nonfraction")
    q = etree.QName(node)
    if q.namespace not in IX_NAMESPACES or q.localname != "nonFraction":
        return stop("not_nonfraction")
    nil = XML_WS.sub(" ", node.get(XSI_NIL, "false")).strip(" ")
    if nil not in ("true", "false", "1", "0"):
        return stop("invalid_nil")
    if nil in ("true", "1"):
        return stop("nil")
    if node.get("continuedAt") is not None:
        return stop("unsupported_structure", "nonfraction_continuation")
    try:
        lexical = _source_text(node)
    except ValueError:
        return stop("unsupported_structure", "child_content")
    if len(lexical) > MAX_TEXT_CHARACTERS:
        return stop("unsupported_resource_bound", "text_length")
    if include_private:
        result["_external_raw_text"] = lexical
    fmt = node.get("format")
    if fmt is not None:
        try:
            namespace, local = _format_qname(node, fmt)
        except (ValueError, TypeError):
            return stop("invalid_format_qname")
        result.update(format_namespace=namespace, format_local=local)
        registry = _registry()
        transform = registry.get(namespace, {}).get(local)
        if transform is None:
            return stop("unsupported_format")
        lexical = XML_WS.sub(" ", lexical).strip(" ")
        try:
            lexical = transform(lexical)
        except Exception as exc:
            return stop("transform_error", type(exc).__name__)
    else:
        lexical = lexical.strip(" \t\r\n")
    if include_private:
        result["_external_transformed_text"] = lexical
    if not isinstance(lexical, str) or not DECIMAL_LEXICAL.fullmatch(lexical):
        # No taxonomy is loaded: exponent/INF might concern other numeric
        # types. This bounded decimal adapter does not pronounce conformance.
        return stop("unsupported_numeric_lexical")
    value = Decimal(lexical)
    if value < 0:
        return stop("invalid_negative_before_sign")
    sign = node.get("sign")
    if sign is not None and sign != "-":
        return stop("invalid_sign")
    scale_text = XML_WS.sub(" ", node.get("scale", "0")).strip(" ")
    if not INTEGER_LEXICAL.fullmatch(scale_text):
        return stop("invalid_scale")
    # Guard integer conversion and arbitrarily large decimal rendering.
    if len(scale_text.lstrip("+-").lstrip("0")) > 6:
        return stop("unsupported_resource_bound", "scale_magnitude")
    scale_digits = scale_text.lstrip("+-").lstrip("0") or "0"
    scale = int(scale_digits) * (-1 if scale_text.startswith("-") else 1)
    if abs(scale) > MAX_ABSOLUTE_SCALE:
        return stop("unsupported_resource_bound", "scale_magnitude")
    result["canonical_value"] = _canonical_scaled(value, scale, sign is not None)
    return stop("normalized")


def dependency_receipt(wheel_path: str | Path, *, additional_wheels=()) -> dict:
    """Hash the pinned wheel and physical imported dependency implementations.

    Call after reference processing so lazily imported modules are represented.
    This inventory covers imported modules, not a syscall-level read trace.
    """
    _registry()
    wheel_path = Path(wheel_path)
    if _sha(wheel_path) != ARELLE_WHEEL_SHA256:
        raise ValueError("pinned Arelle wheel digest mismatch")
    modules = {}
    for name, module in sorted(sys.modules.items()):
        location = getattr(module, "__file__", None)
        if not location:
            continue
        path = Path(location)
        if path.suffix == ".pyc" and path.with_suffix(".py").is_file():
            path = path.with_suffix(".py")
        if not path.is_file():
            continue
        # Imported dependency source/binary files, not input filings or this
        # repository's caller. Standard library and executable are separate.
        if "site-packages" not in str(path) and "arelle_runtime" not in str(path):
            continue
        key = str(path.resolve())
        if key not in modules:
            modules[key] = {"path": key, "bytes": path.stat().st_size,
                            "sha256": _sha(path), "modules": []}
        modules[key]["modules"].append(name)
    distributions = {}
    for name in ("arelle-release", "regex", "isodate", "lxml", "pyparsing", "typing-extensions"):
        try:
            distributions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            distributions[name] = None
    wheels = []
    for path in (wheel_path, *map(Path, additional_wheels)):
        wheels.append({"name": path.name, "bytes": path.stat().st_size, "sha256": _sha(path)})
    return {"scope": "imported_dependency_module_inventory_not_syscall_trace",
            "reference_scope": "numeric_transform_only_no_taxonomy_loading",
            "python": platform.python_version(), "executable_sha256": _sha(Path(sys.executable)),
            "lxml_version": list(etree.LXML_VERSION), "libxml_version": list(etree.LIBXML_VERSION),
            "distributions": distributions, "wheels": wheels,
            "imported_files": list(modules.values()),
            "registry_source_sha256": FUNCTION_IXT_SHA256}


def main():
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--receipt", action="store_true", required=True)
    p.add_argument("--arelle-wheel", type=Path, required=True)
    p.add_argument("--dependency-wheel", type=Path, action="append", default=[])
    args = p.parse_args()
    print(json.dumps(dependency_receipt(args.arelle_wheel,
                     additional_wheels=args.dependency_wheel), indent=2))


if __name__ == "__main__":
    main()
