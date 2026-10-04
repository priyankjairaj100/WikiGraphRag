"""Narrow, byte-ledgered alpha-1 compatibility view; original HTML is immutable."""
from __future__ import annotations
import hashlib
import html as html_module
import re

MAX_PATCHES = 512
ALLOWED_ALPHA = {'color': {'1', '100%'},
                 'background-color': {'0', '1', '100%'},
                 'border-bottom': {'0.01', '1', '100%'}}
ATTR = re.compile(rb'([^\s=<>/]+)\s*=\s*([\'"])(.*?)\2', re.S)
RGBA = re.compile(r'rgba\([ \t\r\n\f]*([0-9]{1,3})[ \t\r\n\f]*,[ \t\r\n\f]*([0-9]{1,3})[ \t\r\n\f]*,[ \t\r\n\f]*([0-9]{1,3})[ \t\r\n\f]*,[ \t\r\n\f]*(1|0\.01|0|100%)[ \t\r\n\f]*\)', re.I)


class CompatibilityRefusal(ValueError):
    pass


def sha(value):
    return hashlib.sha256(value).hexdigest()


def decode_attribute(raw):
    """XML attribute characters and their original UTF-8 byte intervals."""
    value = raw.decode('utf-8')
    chars, spans = [], []
    i, offset = 0, 0
    while i < len(value):
        start = offset
        if value[i] == '&':
            stop = value.find(';', i)
            if stop < 0:
                raise CompatibilityRefusal('unterminated_attribute_entity')
            token = value[i:stop+1]
            decoded = html_module.unescape(token)
            if len(decoded) != 1:
                raise CompatibilityRefusal('non_xml_attribute_entity')
            i = stop + 1
            offset += len(token.encode())
        elif value[i] == '\r' and value[i:i+2] == '\r\n':
            decoded = ' '; i += 2; offset += 2
        else:
            ch = value[i]
            decoded = ' ' if ch in '\r\n\t' else ch
            i += 1; offset += len(ch.encode())
        chars.append(decoded); spans.append((start, offset))
    return ''.join(chars), spans


def declarations(css):
    """Split inline declarations without treating quoted/commented text as CSS."""
    start, i, depth, quote = 0, 0, 0, None
    while i < len(css):
        ch = css[i]
        if quote:
            if ch == quote: quote = None
            i += 1; continue
        if css.startswith('/*', i):
            end = css.find('*/', i+2)
            if end < 0: raise CompatibilityRefusal('unterminated_css_comment')
            i = end+2; continue
        if ch in '\'"': quote = ch
        elif ch == '(': depth += 1
        elif ch == ')':
            depth -= 1
            if depth < 0: raise CompatibilityRefusal('unbalanced_css_parenthesis')
        elif ch == ';' and depth == 0:
            yield start, css[start:i]
            start = i+1
        i += 1
    if quote or depth: raise CompatibilityRefusal('unclosed_css_lexical_context')
    if start < len(css): yield start, css[start:]


def rgba_tokens(value):
    i, quote, depth = 0, None, 0
    while i < len(value):
        if quote:
            if value[i] == quote: quote = None
            i += 1; continue
        if value.startswith('/*', i):
            end = value.find('*/', i+2)
            if end < 0: raise CompatibilityRefusal('unterminated_css_comment')
            i = end+2; continue
        if value[i] in '\'"':
            quote = value[i]; i += 1; continue
        boundary = i == 0 or not (value[i-1].isascii() and value[i-1].isalnum() or ord(value[i-1]) >= 128 or value[i-1] in '-_#')
        if boundary and re.match(r'hsla\s*\(', value[i:], re.I):
            raise CompatibilityRefusal('hsla_outside_qualified_alpha_profile')
        if boundary and re.match(r'rgba\s*\(', value[i:], re.I):
            if depth:
                raise CompatibilityRefusal('nested_rgba_outside_qualified_profile')
            match = RGBA.match(value, i)
            if not match or any(int(match.group(k)) > 255 for k in (1,2,3)):
                raise CompatibilityRefusal('rgba_literal_outside_qualified_profile')
            yield match
            i = match.end(); continue
        if value[i] == '(': depth += 1
        elif value[i] == ')': depth -= 1
        i += 1


def transform(source_html, parse, xhtml_namespace):
    nodes = parse(source_html)
    patches, checked = [], 0
    for node in nodes:
        if node.tag == xhtml_namespace + 'style' and re.search(r'\brgba?\s*\(|\bhsla\s*\(', node.text(), re.I):
            # rgb is harmless but style-sheet color handling is outside this inline-only amendment.
            if re.search(r'\brgba\s*\(|\bhsla\s*\(', node.text(), re.I):
                raise CompatibilityRefusal('stylesheet_alpha_outside_inline_profile')
        opening = source_html[node.start:node.opening_stop]
        for attribute in ATTR.finditer(opening):
            name = attribute.group(1).decode()
            if name.lower() != 'style':
                if name.split(':')[-1].lower() == 'style':
                    raise CompatibilityRefusal('namespaced_style_outside_inline_profile')
                continue
            raw = attribute.group(3)
            css, mapping = decode_attribute(raw)
            if css != node.attrs.get(name):
                raise CompatibilityRefusal('attribute_decoding_mismatch')
            if '\\' in css:
                raise CompatibilityRefusal('css_escape_outside_qualified_profile')
            base = node.start + attribute.start(3)
            for decl_start, declaration in declarations(css):
                colon = declaration.find(':')
                if colon < 0: continue
                prop = declaration[:colon].strip(' \t\r\n\f').lower()
                value = declaration[colon+1:]
                for match in rgba_tokens(value):
                    alpha = match.group(4)
                    if prop not in ALLOWED_ALPHA or alpha not in ALLOWED_ALPHA[prop]:
                        raise CompatibilityRefusal('rgba_property_alpha_pair_outside_qualified_profile')
                    prefix = re.sub(r'/\*.*?\*/', ' ', value[:match.start()], flags=re.S)
                    suffix = re.sub(r'/\*.*?\*/', ' ', value[match.end():], flags=re.S)
                    prefix_pattern = (r'[ \t\r\n\f]*[0-9]+(?:\.[0-9]+)?(?:pt|px)[ \t\r\n\f]+solid[ \t\r\n\f]+'
                                      if prop == 'border-bottom' else r'[ \t\r\n\f]*')
                    if not re.fullmatch(prefix_pattern, prefix, re.I) or not re.fullmatch(r'[ \t\r\n\f]*(?:![ \t\r\n\f]*important)?[ \t\r\n\f]*', suffix, re.I):
                        raise CompatibilityRefusal('rgba_declaration_outside_literal_profile')
                    checked += 1
                    if checked > 2048: raise CompatibilityRefusal('alpha_token_cap')
                    absolute = decl_start + colon + 1
                    a, b = absolute+match.start(), absolute+match.end()
                    raw_token = raw[mapping[a][0]:mapping[b-1][1]]
                    if raw_token.decode() != match.group():
                        raise CompatibilityRefusal('encoded_rgba_token_outside_qualified_profile')
                    if alpha == '1':
                        at = absolute + match.start(4)
                        begin, end = base + mapping[at][0], base + mapping[at][1]
                        if source_html[begin:end] != b'1':
                            raise CompatibilityRefusal('alpha_literal_not_exact_byte_one')
                        patches.append({'byte_start':begin,'byte_stop':end,'old_literal':'1',
                                        'new_literal':'100%','property':prop,'assembly_dom_path':node.path})
                        if len(patches)>MAX_PATCHES: raise CompatibilityRefusal('alpha_patch_cap')
    patches.sort(key=lambda p:p['byte_start'])
    output, cursor = [], 0
    for patch in patches:
        if patch['byte_start'] < cursor: raise CompatibilityRefusal('overlapping_patches')
        output.extend([source_html[cursor:patch['byte_start']],b'100%'])
        cursor=patch['byte_stop']
    output.append(source_html[cursor:]); derived=b''.join(output)
    ledger={'schema_version':'inline_rgba_alpha_compatibility_v17_2',
            'original_assembly_bytes':len(source_html),'original_assembly_sha256':sha(source_html),
            'derived_assembly_bytes':len(derived),'derived_assembly_sha256':sha(derived),
            'checked_rgba_tokens':checked,'patch_count':len(patches),'patches':patches,
            'all_nonpatch_bytes_preserved':True,'original_source_fragment_modified':False}
    return derived,ledger
