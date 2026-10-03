"""Normalize indentation conservatively, preserving literals and Python's parsed structure."""

import ast
import io
import re
import tokenize

from pygments.lexers.c_cpp import CLexer, CppLexer
from pygments.lexers.dotnet import CSharpLexer
from pygments.lexers.go import GoLexer
from pygments.lexers.javascript import JavascriptLexer, TypeScriptLexer
from pygments.lexers.jvm import JavaLexer
from pygments.lexers.rust import RustLexer
from pygments.token import Comment, Error, Literal

LANGUAGES = {
    "c": CLexer, "cpp": CppLexer, "c++": CppLexer,
    "java": JavaLexer, "csharp": CSharpLexer, "c#": CSharpLexer,
    "javascript": JavascriptLexer, "js": JavascriptLexer,
    "typescript": TypeScriptLexer, "ts": TypeScriptLexer,
    "go": GoLexer, "rust": RustLexer,
}


def _code_lexer(text):
    # Require a recognizable code header rather than guessing the language of prose.
    first = text.lstrip()
    if re.match(r"(?:#include\b|using namespace std\b|int main\s*\()", first):
        return CppLexer()
    if re.match(r"(?:import java\.|(?:public\s+)?class\s+\w+\s*\{)", first):
        return JavaLexer()
    if re.match(r"(?:using System\b|namespace\s+\w+)", first):
        return CSharpLexer()
    if re.match(r"package\s+\w+", first):
        return GoLexer()
    if re.match(r"(?:use std\b|(?:pub\s+)?fn main\s*\()", first):
        return RustLexer()
    if re.match(r"(?:interface\s+\w+|type\s+\w+\s*=)", first):
        return TypeScriptLexer()
    if re.match(r"(?:(?:const|let|var|function)[ \t]+\w+|"
                r"import[ \t]+[^\r\n]+[ \t]+from[ \t]+)", first):
        return TypeScriptLexer()  # Also handles ordinary JavaScript tokens.
    return None


def code_tabs(text: str) -> str:
    """Brace languages use tabs; recognized valid Python uses four-space block indentation."""
    try:
        return _format_code(text)
    except Exception:  # noqa: BLE001 — a cosmetic step must never turn a valid answer into an error.
        return text


def _format_code(text):
    fenced = re.fullmatch(r"```([^\n`]*)\n([\s\S]*?)\n```\s*", text)
    if fenced:
        language = fenced[1].strip().lower()
        factory = LANGUAGES.get(language)
        if not factory and language not in ("python", "python3", "py"):
            return text
        body = fenced[2]
        normalized = _python_indent(body) if not factory else _indent(body, factory())
        return text[:fenced.start(2)] + normalized + text[fenced.end(2):]
    lexer = _code_lexer(text)
    if lexer:
        return _indent(text, lexer)
    if re.match(r"(?:import\s+\w|from\s+\w[\w.]*\s+import\b|(?:async\s+)?def\s+\w|"
                r"class\s+\w.*:|for\s+\w.*:|if\s+.*:)", text.lstrip()):
        return _python_indent(text)
    return text


def _python_indent(text):
    original = ast.parse(text)
    lines = text.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    protected = bytearray(len(text))
    stack = [""]
    layout = {}
    literal_types = {tokenize.STRING, getattr(tokenize, "FSTRING_MIDDLE", -1),
                     getattr(tokenize, "TSTRING_MIDDLE", -1)}
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if token.type == tokenize.INDENT:
            stack.append(token.string)
        elif token.type == tokenize.DEDENT:
            stack.pop()
        elif token.type not in (tokenize.ENCODING, tokenize.ENDMARKER):
            layout.setdefault(token.start[0], (stack[-1], len(stack) - 1))
        if token.type in literal_types:
            start = offsets[token.start[0] - 1] + token.start[1]
            end = offsets[token.end[0] - 1] + token.end[1]
            protected[start:end] = b"\x01" * (end - start)
    for row, line in enumerate(lines, 1):
        match = re.match(r"[ \t]+(?=\S)", line)
        if not match or any(protected[offsets[row - 1]:offsets[row - 1] + match.end()]):
            continue
        prefix = match[0]
        block_prefix, depth = layout.get(row, ("", 0))
        if prefix.startswith(block_prefix):
            width = depth * 4 + len(prefix[len(block_prefix):].expandtabs(4))
        else:  # Comment/continuation indentation outside the current block prefix.
            width = len(prefix.expandtabs(4))
        lines[row - 1] = " " * width + line[match.end():]
    result = "".join(lines)
    # Never change an expression, literal, block membership or comment-sensitive syntax.
    if ast.dump(original, include_attributes=False) != ast.dump(ast.parse(result),
                                                              include_attributes=False):
        return text
    return result


def _indent(text, lexer):
    protected = bytearray(len(text))
    # This API preserves offsets; get_tokens() preprocesses tabs and newlines.
    for start, token, value in lexer.get_tokens_unprocessed(text):
        if token in Error:
            return text  # Unknown syntax: preserve the response rather than guessing.
        if token in Literal.String or token in Comment:
            protected[start:start + len(value)] = b"\x01" * len(value)
    lines = []
    offset = 0
    for line in text.splitlines(keepends=True):
        original_size = len(line)
        match = re.match(r"[ \t]+(?=\S)", line)
        if match and not any(protected[offset:offset + match.end()]):
            width = len(match[0].expandtabs(4))
            line = "\t" * (width // 4) + " " * (width % 4) + line[match.end():]
        lines.append(line)
        offset += original_size
    return "".join(lines)
