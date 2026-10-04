"""Bounded, source-exact reported-aspect Inline XBRL reader.

This is a conventional partial numeric reader, not an XBRL validator, natural
language reader, taxonomy processor, financial truth oracle, or novel method.
See docs/typed_reader_contract_v15.txt for the deliberately bounded profile.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
import hashlib
import re
import xml.parsers.expat as expat

IX = "http://www.xbrl.org/2013/inlineXBRL"
XB = "http://www.xbrl.org/2003/instance"
XD = "http://xbrl.org/2006/xbrldi"
XSI = "http://www.w3.org/2001/XMLSchema-instance"
XHTML = "http://www.w3.org/1999/xhtml"
TR15 = "http://www.xbrl.org/inlineXBRL/transformation/2015-02-26"
TR20 = "http://www.xbrl.org/inlineXBRL/transformation/2020-02-12"
TR22 = "http://www.xbrl.org/inlineXBRL/transformation/2022-02-16"
UNCERTAINTIES = ["taxonomy_not_loaded", "dimension_defaults_unresolved",
                 "concept_period_type_unvalidated", "concept_unit_type_unvalidated",
                 "rendering_unverified", "reported_identifier_not_financial_scope"]
# XML 1.0 Fifth Edition NameStartChar/NameChar, excluding colon (NCName).
_START = (r"A-Z_a-z\u00c0-\u00d6\u00d8-\u00f6\u00f8-\u02ff"
          r"\u0370-\u037d\u037f-\u1fff\u200c-\u200d\u2070-\u218f"
          r"\u2c00-\u2fef\u3001-\ud7ff\uf900-\ufdcf\ufdf0-\ufffd\U00010000-\U000effff")
_NC = re.compile("[" + _START + "][" + _START + r".0-9\-\u00b7\u0300-\u036f\u203f-\u2040]*\Z")
_DEC = re.compile(r"\+?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)\Z")
_INT = re.compile(r"[+-]?[0-9]+\Z")
_OLD = re.compile(r"[0-9]{1,3}(?:[, \u00a0]?[0-9]{3})*(?:\.[0-9]+)?\Z")
_NEW = re.compile(r"[, \u00a00-9]*(?:\.[ \u00a00-9]+)?\Z")


class ReaderError(ValueError):
    """Unsafe, malformed, or outside-bounds input document/query."""


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _tag(uri, local):
    return "{" + uri + "}" + local


def _expanded(name):
    return "{" + name if "}" in name else name


def _ws(text):
    # XML Schema collapse: NBSP is intentionally not XML whitespace.
    return re.sub(r"[ \t\r\n]+", " ", text).strip(" \t\r\n")


def _idref(node, key):
    raw = node.attrs.get(key)
    return _ws(raw) if raw is not None else None


def _tag_end(source, start):
    quote = None
    for i in range(start, len(source)):
        char = source[i]
        if quote is not None:
            if char == quote:
                quote = None
        elif char in (34, 39):
            quote = char
        elif char == 62:
            return i + 1
    raise ReaderError("unterminated XML tag")


@dataclass
class _Node:
    tag: str
    attrs: dict
    ns: dict
    ns_spans: dict
    start: int
    opening_stop: int
    self_closing: bool
    path: str
    parent: object = None
    stop: int = 0
    parts: list = field(default_factory=list)
    sibling_counts: dict = field(default_factory=lambda: defaultdict(int))

    @property
    def children(self):
        return [x for x in self.parts if isinstance(x, _Node)]

    def text(self):
        return "".join(x.text() if isinstance(x, _Node) else x for x in self.parts)

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()

    def ancestors(self):
        node = self.parent
        while node is not None:
            yield node
            node = node.parent


def _parse(source):
    parser = expat.ParserCreate(namespace_separator="}")
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    stack, nodes, pending = [], [], {}

    def reject(*_):
        raise ReaderError("DTD/entity declarations and external entities are forbidden")

    def declaration(version, encoding, standalone):
        if encoding and encoding.lower().replace("-", "") != "utf8":
            raise ReaderError("only UTF-8 XML is supported for byte anchors")

    def start_namespace(prefix, uri):
        pending[prefix or ""] = uri or ""

    def start(name, attrs):
        if len(stack) >= 256 or len(nodes) >= 1000000:
            raise ReaderError("XML depth/node bound exceeded")
        begin = parser.CurrentByteIndex
        end = _tag_end(source, begin)
        parent = stack[-1] if stack else None
        tag = _expanded(name)
        ns = dict(parent.ns) if parent else {"xml": "http://www.w3.org/XML/1998/namespace"}
        origins = dict(parent.ns_spans) if parent else {}
        for prefix, uri in pending.items():
            ns[prefix] = uri
            origins[prefix] = (begin, end)
        pending.clear()
        if parent:
            parent.sibling_counts[tag] += 1
            path = parent.path + "/" + tag + "[" + str(parent.sibling_counts[tag]) + "]"
        else:
            path = "/" + tag + "[1]"
        node = _Node(tag, {_expanded(k): v for k, v in attrs.items()}, ns, origins,
                     begin, end, source[begin:end].rstrip().endswith(b"/>"), path, parent)
        if parent:
            parent.parts.append(node)
        nodes.append(node)
        stack.append(node)

    def end(name):
        node = stack.pop()
        node.stop = node.opening_stop if node.self_closing else _tag_end(source, parser.CurrentByteIndex)

    def characters(text):
        if stack:
            stack[-1].parts.append(text)

    parser.XmlDeclHandler = declaration
    parser.StartDoctypeDeclHandler = reject
    parser.EntityDeclHandler = reject
    parser.ExternalEntityRefHandler = reject
    parser.StartNamespaceDeclHandler = start_namespace
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = characters
    try:
        parser.Parse(source, True)
    except expat.ExpatError as exc:
        raise ReaderError("malformed XML: " + str(exc)) from exc
    if not nodes or nodes[0].tag != _tag(XHTML, "html"):
        raise ReaderError("bounded profile requires XHTML html root")
    return nodes


def _qname(node, lexical):
    if lexical is None:
        return None
    lexical = _ws(lexical)
    parts = lexical.split(":")
    if len(parts) not in (1, 2) or any(not _NC.fullmatch(p) for p in parts):
        return None
    prefix, local = parts if len(parts) == 2 else ("", parts[0])
    uri = node.ns.get(prefix)
    return _tag(uri, local) if uri else None


def _plain(node):
    return not node.children and bool(_ws(node.text()))


def _date_value(lexical, *, end_boundary):
    """Exact calendar boundary: (day ordinal, Decimal seconds, tz-known).

    Supports years 0001..9999, dates/dateTimes, exact fractional seconds and
    XML timezone offsets. Date-only end/instant is next day's midnight.
    """
    pattern = (r"([0-9]{4})-([0-9]{2})-([0-9]{2})"
               r"(?:T([0-9]{2}):([0-9]{2}):([0-9]{2})(\.[0-9]+)?)?"
               r"(Z|[+-][0-9]{2}:[0-9]{2})?\Z")
    match = re.fullmatch(pattern, lexical)
    if not match:
        if re.match(r"-?[0-9]{5,}-|-[0-9]{4}-", lexical):
            raise ReaderError("extended/BCE years outside bounded calendar profile")
        raise ValueError("date lexical outside bounded ISO profile")
    yy, mm, dd, hour, minute, second, fraction, zone = match.groups()
    if fraction and len(fraction) > 4097:
        raise ReaderError("dateTime fractional precision bound exceeded")
    day = date(int(yy), int(mm), int(dd)).toordinal()
    seconds = Decimal(0)
    if hour is None:
        if end_boundary:
            day += 1
    else:
        h, m, s = int(hour), int(minute), int(second)
        if m > 59 or s > 59 or h > 24 or (h == 24 and (m or s or (fraction and Decimal(fraction) != 0))):
            raise ValueError("invalid dateTime clock")
        # Construct exact seconds directly, rather than Decimal addition.
        seconds = Decimal(str(h * 3600 + m * 60 + s) + (fraction or ""))
    if zone and zone != "Z":
        zh, zm = int(zone[1:3]), int(zone[4:6])
        if zh > 14 or zm > 59 or (zh == 14 and zm):
            raise ValueError("invalid timezone offset")
        # Seconds + zone uses integers/fractions as a tuple to avoid the caller's
        # Decimal context. Compare boundaries later using integer coefficients.
        offset = (zh * 60 + zm) * 60 * (1 if zone[0] == "+" else -1)
    else:
        offset = 0
    return {"day_ordinal": day, "seconds": str(seconds), "timezone_offset_seconds": offset,
            "timezone_specified": zone is not None,
            "date_only_end_advanced": hour is None and end_boundary}


def _exact_decimal_ratio(text):
    tup = Decimal(text).as_tuple()
    coefficient = int("".join(str(d) for d in tup.digits)) * (-1 if tup.sign else 1)
    if tup.exponent >= 0:
        return coefficient * 10 ** tup.exponent, 1
    return coefficient, 10 ** (-tup.exponent)


def _boundary_ratio(boundary):
    numerator, denominator = _exact_decimal_ratio(boundary["seconds"])
    whole = boundary["day_ordinal"] * 86400 - boundary["timezone_offset_seconds"]
    return whole * denominator + numerator, denominator


def _context(node, anchor):
    invalid, unresolved = [], []
    children = node.children
    tags = [c.tag for c in children]
    valid_sequences = [[_tag(XB, "entity"), _tag(XB, "period")],
                       [_tag(XB, "entity"), _tag(XB, "period"), _tag(XB, "scenario")]]
    if tags not in valid_sequences:
        invalid.append("context_child_shape_invalid")
    if any(isinstance(x, str) and _ws(x) for x in node.parts):
        invalid.append("context_container_text_invalid")
    entities = [c for c in children if c.tag == _tag(XB, "entity")]
    entity = None
    containers = [c for c in children if c.tag == _tag(XB, "scenario")]
    if len(entities) == 1:
        e = entities[0]
        if any(isinstance(x, str) and _ws(x) for x in e.parts):
            invalid.append("entity_container_text_invalid")
        ets = [c.tag for c in e.children]
        if ets not in [[_tag(XB, "identifier")], [_tag(XB, "identifier"), _tag(XB, "segment")]]:
            invalid.append("entity_child_shape_invalid")
        identifiers = [c for c in e.children if c.tag == _tag(XB, "identifier")]
        containers += [c for c in e.children if c.tag == _tag(XB, "segment")]
        if len(identifiers) == 1 and _plain(identifiers[0]) and _ws(identifiers[0].attrs.get("scheme", "")):
            entity = {"scheme": _ws(identifiers[0].attrs["scheme"]),
                      "identifier": _ws(identifiers[0].text())}
        else:
            invalid.append("entity_identifier_invalid")
    else:
        invalid.append("entity_cardinality_invalid")
    periods = [c for c in children if c.tag == _tag(XB, "period")]
    period, boundaries = None, None
    if len(periods) == 1:
        p = periods[0]
        if any(isinstance(x, str) and _ws(x) for x in p.parts):
            invalid.append("period_container_text_invalid")
        names = [c.tag.removeprefix("{" + XB + "}") if c.tag.startswith("{" + XB + "}") else c.tag for c in p.children]
        if names in [["instant"], ["startDate", "endDate"], ["forever"]]:
            lexemes = {name: _ws(c.text()) for name, c in zip(names, p.children)}
            kind = "duration" if len(names) == 2 else names[0]
            period = {"kind": kind, "lexemes": lexemes}
            if any(c.children for c in p.children) or (kind == "forever" and lexemes["forever"]):
                invalid.append("period_content_invalid")
            else:
                try:
                    boundaries = {name: _date_value(value, end_boundary=name != "startDate")
                                  for name, value in lexemes.items() if name != "forever"}
                    if kind == "duration":
                        start, end = boundaries["startDate"], boundaries["endDate"]
                        if start["timezone_specified"] != end["timezone_specified"]:
                            unresolved.append("period_timezone_comparison_unresolved")
                        else:
                            sn, sd = _boundary_ratio(start)
                            en, ed = _boundary_ratio(end)
                            if sn * ed >= en * sd:
                                invalid.append("duration_not_positive")
                except ReaderError:
                    unresolved.append("period_date_outside_profile")
                except (ValueError, OverflowError):
                    invalid.append("period_date_invalid")
        else:
            invalid.append("period_child_shape_invalid")
    else:
        invalid.append("period_cardinality_invalid")
    dimensions, extras = [], []
    for container in containers:
        placement = container.tag.rsplit("}", 1)[-1]
        if any(isinstance(p, str) and _ws(p) for p in container.parts):
            invalid.append("dimensional_container_text_invalid")
        for child in container.children:
            if child.tag not in (_tag(XD, "explicitMember"), _tag(XD, "typedMember")):
                extras.append(anchor(child))
                unresolved.append("non_dimensional_context_content_uninterpreted")
                continue
            dim = _qname(child, child.attrs.get("dimension"))
            if not dim:
                invalid.append("dimension_qname_unresolved")
            entry = {"kind": child.tag.rsplit("}", 1)[-1], "dimension": dim, "placement": placement}
            if entry["kind"] == "explicitMember":
                member = _qname(child, child.text()) if _plain(child) else None
                entry["member"] = member
                if not member:
                    invalid.append("dimension_member_qname_unresolved")
            else:
                entry["typed_content_anchor"] = anchor(child)
                if len(child.children) != 1 or any(isinstance(x, str) and _ws(x) for x in child.parts):
                    invalid.append("typed_dimension_shape_invalid")
                unresolved.append("typed_dimension_semantics_unresolved")
            dimensions.append(entry)
    if any(n > 1 for n in Counter(d["dimension"] for d in dimensions).values()):
        invalid.append("repeated_dimension")
    dimensions.sort(key=lambda d: (d["dimension"] or "", d["placement"]))
    if any(a.attrs.get("target") for a in node.ancestors() if a.tag == _tag(IX, "resources")):
        unresolved.append("nondefault_resource_target_unsupported")
    return {"id": _idref(node, "id"), "entity": entity, "period": period,
            "period_boundaries": boundaries, "dimensions": dimensions, "extra_content_anchors": extras,
            "anchor": anchor(node), "invalid_issues": sorted(set(invalid)),
            "unresolved_issues": sorted(set(unresolved)), "uncertainties": list(UNCERTAINTIES)}


def _unit(node, anchor):
    invalid, unresolved = [], []
    cs = node.children
    out = {"shape": None, "measures": [], "numerator_measures": [], "denominator_measures": []}

    def measures(nodes):
        values = []
        if not nodes:
            invalid.append("unit_empty_measure_product")
        for child in nodes:
            qname = _qname(child, child.text()) if child.tag == _tag(XB, "measure") and _plain(child) else None
            if qname is None:
                invalid.append("unit_measure_qname_or_shape_invalid")
            values.append(qname)
        return sorted(values, key=lambda x: x or "")

    if cs and all(c.tag == _tag(XB, "measure") for c in cs):
        out.update(shape="simple_product", measures=measures(cs))
    elif len(cs) == 1 and cs[0].tag == _tag(XB, "divide"):
        div = cs[0].children
        if [c.tag for c in div] == [_tag(XB, "unitNumerator"), _tag(XB, "unitDenominator")]:
            out.update(shape="divide", numerator_measures=measures(div[0].children),
                       denominator_measures=measures(div[1].children))
            if set(out["numerator_measures"]) & set(out["denominator_measures"]):
                invalid.append("unit_numerator_denominator_common_measure")
        else:
            invalid.append("unit_divide_shape_invalid")
    else:
        invalid.append("unit_child_shape_invalid")
    for part in node.walk():
        if part.tag != _tag(XB, "measure") and any(isinstance(x, str) and _ws(x) for x in part.parts):
            invalid.append("unit_container_text_invalid")
    if any(a.attrs.get("target") for a in node.ancestors() if a.tag == _tag(IX, "resources")):
        unresolved.append("nondefault_resource_target_unsupported")
    return {"id": _idref(node, "id"), "reported_unit": out, "anchor": anchor(node),
            "invalid_issues": sorted(set(invalid)), "unresolved_issues": sorted(set(unresolved))}


def _integer(lexical, bound):
    lexical = _ws(lexical)
    if not _INT.fullmatch(lexical):
        raise ValueError("integer lexical")
    if len(lexical.lstrip("+-").lstrip("0")) > 6:
        raise ReaderError("integer bound")
    result = int(("-" if lexical.startswith("-") else "") + (lexical.lstrip("+-").lstrip("0") or "0"))
    if abs(result) > bound:
        raise ReaderError("integer bound")
    return result


def _numeric(node):
    invalid, unsupported = [], []
    attrs = node.attrs
    nil_lexical = _ws(attrs.get(_tag(XSI, "nil"), "false"))
    nil = nil_lexical in ("true", "1")
    if nil_lexical not in ("true", "1", "false", "0"):
        invalid.append("nil_lexical_invalid")
    if attrs.get("sign") not in (None, "-"):
        invalid.append("sign_lexical_invalid")
    try:
        scale = _integer(attrs.get("scale", "0"), 10000)
    except ReaderError:
        scale = None
        unsupported.append("scale_outside_profile")
    except ValueError:
        scale = None
        invalid.append("scale_lexical_invalid")
    decimals, precision = attrs.get("decimals"), attrs.get("precision")
    decimals = _ws(decimals) if decimals is not None else None
    precision = _ws(precision) if precision is not None else None
    if nil:
        if decimals is not None or precision is not None:
            invalid.append("nil_with_accuracy_attribute")
    elif (decimals is None) == (precision is None):
        invalid.append("accuracy_attribute_cardinality_invalid")
    precision_is_zero = False
    for key, value in (("decimals", decimals), ("precision", precision)):
        if value is not None and value != "INF":
            try:
                parsed = _integer(value, 100000)
                if key == "precision" and parsed < 0:
                    raise ValueError("precision must be nonnegative")
                if key == "precision" and parsed == 0:
                    precision_is_zero = True
            except ReaderError:
                unsupported.append(key + "_outside_profile")
            except ValueError:
                invalid.append(key + "_lexical_invalid")
    children = node.children
    if nil:
        # Inline nil may retain a displayed placeholder; conversion suppresses
        # its target numeric text. It must never become zero via a transform.
        if children:
            unsupported.append("nil_child_elements_outside_profile")
        if any(a.tag == _tag(IX, "nonFraction") for a in node.ancestors()):
            invalid.append("nil_nested_in_nonfraction")
    elif children:
        if len(children) != 1 or children[0].tag != _tag(IX, "nonFraction") or any(
                isinstance(x, str) and _ws(x) for x in node.parts):
            invalid.append("nonfraction_content_shape_invalid")
    elif not node.text():
        invalid.append("nonfraction_empty_text")
    fmt = _qname(node, attrs.get("format"))
    if "format" in attrs and fmt is None:
        invalid.append("format_qname_unresolved")
    # Every nested occurrence is read from its own raw content, never the
    # already transformed/signed/scaled value of an inner occurrence.
    relatives = [n for n in [*node.ancestors(), *list(node.walk())[1:]] if n.tag == _tag(IX, "nonFraction")]
    for relative in relatives:
        ancestor_format = _qname(relative, relative.attrs.get("format"))
        try:
            ancestor_scale = _integer(relative.attrs.get("scale", "0"), 10000)
        except ValueError:
            ancestor_scale = None
        if fmt != ancestor_format or scale != ancestor_scale or _idref(node, "unitRef") != _idref(relative, "unitRef"):
            invalid.append("nested_format_scale_unit_mismatch")
    for descendant in list(node.walk())[1:]:
        if descendant.tag != _tag(IX, "nonFraction"):
            invalid.append("nonfraction_descendant_content_shape_invalid")
        elif _ws(descendant.attrs.get(_tag(XSI, "nil"), "false")) in ("true", "1"):
            invalid.append("nil_nested_in_nonfraction")
        elif descendant.children and (len(descendant.children) != 1 or descendant.children[0].tag != _tag(IX, "nonFraction") or any(isinstance(x, str) and _ws(x) for x in descendant.parts)):
            invalid.append("nonfraction_descendant_content_shape_invalid")
    raw = node.text()
    transformed = None
    if not nil and not invalid:
        lexical = _ws(raw) if fmt else raw.strip(" \t\r\n")
        if fmt == _tag(TR15, "numdotdecimal"):
            if _OLD.fullmatch(lexical):
                transformed = lexical.replace(",", "").replace(" ", "").replace("\u00a0", "")
            else:
                invalid.append("transformation_lexical_invalid")
        elif fmt in {_tag(ns, "num-dot-decimal") for ns in (TR20, TR22)}:
            if _NEW.fullmatch(lexical):
                transformed = lexical.replace(",", "").replace(" ", "").replace("\u00a0", "")
                if not _DEC.fullmatch(transformed):
                    invalid.append("transformation_lexical_invalid")
            else:
                invalid.append("transformation_lexical_invalid")
        elif fmt in {_tag(ns, "fixed-zero") for ns in (TR20, TR22)}:
            transformed = "0"
        elif fmt is not None:
            unsupported.append("transformation_not_supported")
        elif _DEC.fullmatch(lexical):
            transformed = lexical
        elif re.search(r"[eE]", lexical) and re.fullmatch(r"\+?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)[eE][+-]?[0-9]+", lexical):
            unsupported.append("untransformed_scientific_notation_outside_decimal_profile")
        else:
            invalid.append("untransformed_nonnegative_decimal_lexical_invalid")
    value = None
    if transformed is not None and not invalid and not unsupported:
        if sum(c.isdigit() for c in transformed) > 4096:
            unsupported.append("numeric_digit_bound_exceeded")
        else:
            number = Decimal(transformed)
            parts = number.as_tuple()
            exact = Decimal((1 if attrs.get("sign") == "-" else 0, parts.digits, parts.exponent + scale))
            value = format(exact, "f")
            if "." in value:
                value = value.rstrip("0").rstrip(".")
            if exact.is_zero():
                value = "0"
    return {"numeric_status": "invalid" if invalid else "unsupported" if unsupported else "nil" if nil else "normalized",
            "normalized_value": value, "lexical_text": raw, "format_qname": fmt,
            "scale_property": scale, "nil": nil,
            "accuracy": {"decimals": decimals, "precision": precision,
                         "interpretation": "no_accuracy_information" if precision_is_zero else "metadata_only_not_rounding_or_scaling"},
            "invalid_issues": sorted(set(invalid)), "unresolved_issues": sorted(set(unsupported))}


def read_inline_xbrl(source: bytes, *, source_version: str, max_source_bytes=25 * 1024 * 1024):
    """Read one immutable UTF-8 document, no network, external schemas or files.

    Returned detailed records contain source values and must remain in the
    caller's permitted external artifact location for unredistributable sources.
    """
    if not isinstance(source, bytes) or not source or len(source) > max_source_bytes:
        raise ReaderError("source must be nonempty bounded bytes")
    if not isinstance(source_version, str) or not source_version.strip():
        raise ReaderError("source_version must be a nonempty explicit physical-version label")
    # Without an XML declaration Expat also auto-detects UTF-16; anchors here
    # deliberately support UTF-8 only.
    if b"\x00" in source[:128] or source.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise ReaderError("only UTF-8 XML is supported")
    digest = _hash(source)
    nodes = _parse(source)

    def span(begin, end, path=None):
        result = {"source_sha256": digest, "source_version": source_version,
                  "byte_start": begin, "byte_stop": end, "span_sha256": _hash(source[begin:end])}
        if path is not None:
            result["dom_path"] = path
        return result

    def anchor(node):
        return span(node.start, node.stop, node.path)

    ids = Counter(_idref(n, "id") for n in nodes if "id" in n.attrs)
    duplicated = sorted(k for k, n in ids.items() if n > 1)
    contexts, units = [], []
    context_nodes, unit_nodes = defaultdict(list), defaultdict(list)
    for node in nodes:
        if node.tag == _tag(XB, "context"):
            record = _context(node, anchor)
            contexts.append(record)
            context_nodes[_idref(node, "id")].append((record, node))
        elif node.tag == _tag(XB, "unit"):
            record = _unit(node, anchor)
            units.append(record)
            unit_nodes[_idref(node, "id")].append((record, node))
        else:
            continue
        if not _NC.fullmatch(_idref(node, "id") or ""):
            record["invalid_issues"].append("resource_id_invalid_or_outside_profile")
        if ids[_idref(node, "id")] > 1:
            record["invalid_issues"].append("resource_id_not_unique")
    facts = []
    for node in nodes:
        if node.tag != _tag(IX, "nonFraction"):
            continue
        numeric = _numeric(node)
        invalid = list(numeric["invalid_issues"])
        unresolved = list(numeric["unresolved_issues"])
        concept = _qname(node, node.attrs.get("name"))
        if concept is None:
            invalid.append("concept_qname_unresolved")
        if "id" in node.attrs and not _NC.fullmatch(_idref(node, "id")):
            invalid.append("fact_id_invalid_or_outside_profile")
        if duplicated:
            invalid.append("document_duplicate_ids")
        if node.attrs.get("target"):
            unresolved.append("nondefault_fact_target_unsupported")
        if node.attrs.get("tupleRef") or any(a.tag == _tag(IX, "tuple") for a in node.ancestors()):
            unresolved.append("tuple_membership_unsupported")
        dependencies = [anchor(node)]
        dependency_nodes = [node]
        linked = {}
        for key, pool in (("contextRef", context_nodes), ("unitRef", unit_nodes)):
            ref = _idref(node, key)
            matches = pool.get(ref, [])
            if ref is None or not _NC.fullmatch(ref):
                invalid.append(key + "_invalid_or_outside_profile")
            if len(matches) != 1:
                invalid.append(key + "_missing_or_ambiguous")
                linked[key] = None
            else:
                record, target_node = matches[0]
                linked[key] = record
                invalid += record["invalid_issues"]
                unresolved += record["unresolved_issues"]
                dependencies.append(anchor(target_node))
                dependency_nodes += list(target_node.walk())
        # Nested constraints refer to ancestor attributes: include those bytes.
        for ancestor in node.ancestors():
            if ancestor.tag == _tag(IX, "nonFraction"):
                dependencies.append(anchor(ancestor))
                dependency_nodes.append(ancestor)
        origins = {value for dep in dependency_nodes for value in dep.ns_spans.values()}
        dependencies += [span(begin, end) for begin, end in sorted(origins)]
        unique = {(a["byte_start"], a["byte_stop"]): a for a in dependencies}
        dependencies = [unique[k] for k in sorted(unique)]
        context, unit = linked["contextRef"], linked["unitRef"]
        binding_invalid = [x for x in invalid if x not in numeric["invalid_issues"]]
        binding_unresolved = [x for x in unresolved if x not in numeric["unresolved_issues"]]
        aspects = {"concept": concept, "entity": context["entity"] if context else None,
                   "period": context["period"] if context else None,
                   "unit": unit["reported_unit"] if unit else None,
                   "dimensions": context["dimensions"] if context else None}
        c_issues = context["invalid_issues"] + context["unresolved_issues"] if context else ["context_missing"]
        context_shape_valid = context is not None and not any(x.startswith(("context_", "resource_")) for x in c_issues)
        resolved_aspects = {
            "concept": concept is not None,
            "entity": context_shape_valid and not any(x.startswith("entity_") for x in c_issues),
            "period": context_shape_valid and not any(x.startswith(("period_", "duration_")) for x in c_issues),
            "dimensions": context_shape_valid and not any(x.startswith(("dimension", "repeated_dimension", "typed_dimension", "non_dimensional")) for x in c_issues),
            "unit": unit is not None and not unit["invalid_issues"] and not unit["unresolved_issues"],
        }
        fact = {"fact_ordinal": len(facts), "source_sha256": digest, "source_version": source_version,
                "attributes": dict(node.attrs), "concept": concept,
                "context_id": _idref(node, "contextRef"), "unit_id": _idref(node, "unitRef"),
                **numeric, "reported_aspects": aspects,
                "resolved_aspects": resolved_aspects,
                "binding_status": "invalid" if binding_invalid else "unresolved" if binding_unresolved else "reported_aspects_resolved",
                "invalid_issues": sorted(set(invalid)), "unresolved_issues": sorted(set(unresolved)),
                "status": "invalid" if invalid else "unresolved" if unresolved else numeric["numeric_status"],
                "issues": sorted(set(invalid + unresolved)), "anchor": anchor(node),
                "evidence_anchors": dependencies, "uncertainties": list(UNCERTAINTIES),
                "visibility": "hidden_markup" if any(a.tag in (_tag(IX, "hidden"), _tag(IX, "header")) for a in node.ancestors()) else "rendering_unverified",
                "nested_occurrence": any(a.tag == _tag(IX, "nonFraction") for a in node.ancestors())}
        facts.append(fact)
    return {"schema": "bounded_inline_xbrl_reported_reader_v15", "source_sha256": digest,
            "source_version": source_version, "source_bytes": len(source), "contexts": contexts,
            "units": units, "facts": facts, "duplicate_ids": duplicated,
            "issues": ["document_duplicate_ids"] if duplicated else [],
            "uncertainties": list(UNCERTAINTIES), "external_resources_loaded": 0,
            "taxonomy_validated": False, "natural_language_qa": False}


def make_reported_query(document, fact):
    """Construct a complete structured query for a resolved reported occurrence.

    This helper is for binding diagnostics, not automatic question understanding.
    """
    if fact not in document["facts"] or fact["binding_status"] != "reported_aspects_resolved":
        raise ReaderError("query helper requires this document's resolved reported occurrence")
    return {"source_sha256": document["source_sha256"], "source_version": document["source_version"],
            "scope_mode": "reported_aspects", **deepcopy(fact["reported_aspects"])}


def _expanded_qname_valid(value):
    if not isinstance(value, str) or not value.startswith("{") or "}" not in value:
        return False
    uri, local = value[1:].rsplit("}", 1)
    return bool(uri) and bool(_NC.fullmatch(local))


def _check_query(query):
    if not _expanded_qname_valid(query["concept"]):
        raise ReaderError("expanded concept QName is required")
    entity = query["entity"]
    if not isinstance(entity, dict) or set(entity) != {"scheme", "identifier"} or not all(isinstance(v, str) and _ws(v) for v in entity.values()):
        raise ReaderError("complete entity binding required")
    period = query["period"]
    if not isinstance(period, dict) or set(period) != {"kind", "lexemes"} or not isinstance(period["lexemes"], dict):
        raise ReaderError("complete period binding required")
    names = {"instant": {"instant"}, "duration": {"startDate", "endDate"}, "forever": {"forever"}}
    if not isinstance(period["kind"], str) or period["kind"] not in names or set(period["lexemes"]) != names[period["kind"]] or not all(isinstance(v, str) for v in period["lexemes"].values()):
        raise ReaderError("period query shape invalid")
    try:
        boundaries = {}
        for key, value in period["lexemes"].items():
            if key == "forever":
                if value:
                    raise ValueError("forever content")
            else:
                boundaries[key] = _date_value(value, end_boundary=key != "startDate")
        if period["kind"] == "duration":
            start, end = boundaries["startDate"], boundaries["endDate"]
            if start["timezone_specified"] != end["timezone_specified"]:
                raise ValueError("query duration timezone is unresolved")
            sn, sd = _boundary_ratio(start)
            en, ed = _boundary_ratio(end)
            if sn * ed >= en * sd:
                raise ValueError("query duration is not positive")
    except (ValueError, OverflowError) as exc:
        raise ReaderError("period query outside valid supported calendar profile") from exc
    unit = query["unit"]
    if not isinstance(unit, dict) or set(unit) != {"shape", "measures", "numerator_measures", "denominator_measures"}:
        raise ReaderError("complete unit binding required")
    for key in ("measures", "numerator_measures", "denominator_measures"):
        if not isinstance(unit[key], list) or not all(_expanded_qname_valid(v) for v in unit[key]):
            raise ReaderError("unit query requires expanded measure QNames")
        unit[key] = sorted(unit[key])
    if not ((unit["shape"] == "simple_product" and unit["measures"] and not unit["numerator_measures"] and not unit["denominator_measures"]) or
            (unit["shape"] == "divide" and not unit["measures"] and unit["numerator_measures"] and unit["denominator_measures"] and not set(unit["numerator_measures"]) & set(unit["denominator_measures"]))):
        raise ReaderError("unit query shape invalid")
    dimensions = query["dimensions"]
    if not isinstance(dimensions, list):
        raise ReaderError("explicit dimension list required, including [] when absent")
    for dim in dimensions:
        if not isinstance(dim, dict) or set(dim) != {"kind", "dimension", "member", "placement"} or dim["kind"] != "explicitMember" or dim["placement"] not in ("segment", "scenario") or not _expanded_qname_valid(dim["dimension"]) or not _expanded_qname_valid(dim["member"]):
            raise ReaderError("only completely specified reported explicit dimensions are queryable")
    if len({d["dimension"] for d in dimensions}) != len(dimensions):
        raise ReaderError("query repeats a dimension")
    dimensions.sort(key=lambda d: (d["dimension"], d["placement"]))


def lookup(document, query):
    """Return all compatible occurrences; never relax, rank, merge or pick first."""
    required = {"source_sha256", "source_version", "scope_mode", "concept", "entity", "period", "unit", "dimensions"}
    if not isinstance(query, dict) or set(query) != required:
        raise ReaderError("query must specify exactly the complete reported-aspect bindings")
    if query["source_sha256"] != document["source_sha256"] or query["source_version"] != document["source_version"]:
        raise ReaderError("source object/version mismatch; cross-document lookup cannot merge")
    if query["scope_mode"] != "reported_aspects":
        raise ReaderError("only explicitly reported aspects are supported")
    query = deepcopy(query)
    _check_query(query)
    candidates = []
    for fact in document["facts"]:
        aspects = fact["reported_aspects"]
        # Unresolved bindings cannot be used to exclude a possible match.
        matches = True
        for key in ("concept", "entity", "period", "unit", "dimensions"):
            value = aspects[key]
            if fact["resolved_aspects"][key] and value is not None and value != query[key]:
                matches = False
                break
        if matches:
            candidates.append(fact)
    valid = [f for f in candidates if f["status"] == "normalized"]
    blockers = [f for f in candidates if f["status"] != "normalized"]
    values = sorted({f["normalized_value"] for f in valid})
    if blockers:
        status = "blocked"
    elif not candidates:
        status = "absent"
    elif len(values) > 1:
        status = "ambiguous"
    elif len(candidates) > 1:
        status = "multiple_occurrences_same_value"
    else:
        status = "unique_reported_value"
    anchors = {(a["byte_start"], a["byte_stop"]): a for f in candidates for a in f["evidence_anchors"]}
    return {"status": status, "candidate_ordinals": [f["fact_ordinal"] for f in candidates],
            "candidates": candidates, "reported_values": values, "blocking_ordinals": [f["fact_ordinal"] for f in blockers],
            "evidence_anchors": [anchors[k] for k in sorted(anchors)],
            "uncertainties": list(UNCERTAINTIES), "query": query,
            "financial_or_natural_language_answer_certified": False}
