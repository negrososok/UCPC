"""Whitespace conversion must never change executable data or Python block syntax."""

import pytest

from ucpc.code_format import code_tabs
from ucpc.history import Track


@pytest.mark.parametrize("header, statement", [
    ("#include <iostream>\nint main() {", 'std::cout << "    text";'),
    ("public class Main {", 'String x = "    text";'),
    ("using System;\nclass Program {", 'string x = "    text";'),
    ("package main\nfunc main() {", 'x := "    text"'),
    ("fn main() {", 'let x = "    text";'),
    ("const main = () => {", 'const x = "    text";'),
    ("interface Item {", "value: number;"),
])
def test_brace_languages_get_tabs_without_changing_other_bytes(header, statement):
    source = header + "\r\n    " + statement + "\r\n        next();\r\n}\r\n"
    expected = header + "\r\n\t" + statement + "\r\n\t\tnext();\r\n}\r\n"
    assert code_tabs(source) == expected
    assert code_tabs(expected) == expected  # Idempotent for copy/history refresh.


@pytest.mark.parametrize("header, literal", [
    ("#include <string>\nint main() {", 'auto s = R"TAG(first\n    keep\n        keep too)TAG";'),
    ("public class Main {", 'String s = """\n    keep\n        keep too\n    """;'),
    ("using System;\nclass Program {", 'string s = @"first\n    keep\n        keep too";'),
    ("const main = () => {", 'const s = `first\n    keep\n        keep too`;'),
    ("package main\nfunc main() {", 's := `first\n    keep\n        keep too`'),
    ("fn main() {", 'let s = r#"first\n    keep\n        keep too"#;'),
])
def test_multiline_literal_contents_are_unchanged(header, literal):
    source = header + "\n    " + literal + "\n    next();\n}\n"
    result = code_tabs(source)
    assert literal in result
    assert result.endswith("\n\tnext();\n}\n")


def test_comments_blank_lines_alignment_and_preprocessor_are_preserved():
    source = '#include <iostream>\nint main() {\n    /* comment\n        formatted diagram\n    */\n\n    f(\n      1);\n}\n'
    result = code_tabs(source)
    assert "/* comment\n        formatted diagram\n    */" in result
    assert "\n\n\tf(\n\t  1);" in result
    assert result.count("\n") == source.count("\n")


@pytest.mark.parametrize("text", [
    "n = int(input())\nfor i in range(n):\n    if i > 0:\n        print(i)\n",
    'def f():\n    return """literal\n    unchanged\n    """\n',
    "Відповідь:\n    const value = 1;\n",
    "Потрібна повна умова.\n    Будь ласка, додай обмеження.\n",
    "```python\nfor i in range(3):\n    print(i)\n```",
    "unknown syntax\n    untouched\n",
])
def test_python_prose_and_unknown_languages_are_unchanged(text):
    assert code_tabs(text) == text


def test_explicit_fence_handles_code_without_header():
    assert code_tabs("```cpp\nvoid f() {\n    run();\n}\n```") == (
        "```cpp\nvoid f() {\n\trun();\n}\n```"
    )


def test_completed_or_cancelled_track_cannot_be_rewritten():
    track = Track(text="int main() {\n    return 0;\n}\n")
    track.finish("Скасовано")
    track.format_text(code_tabs)
    assert track.snapshot() == ("int main() {\n    return 0;\n}\n", True, "Скасовано")


def test_formatting_keeps_track_size_limit():
    track = Track(text="x", max_text_chars=4)
    with pytest.raises(RuntimeError):
        track.format_text(lambda _: "too long")
    assert track.text == "x"


@pytest.mark.parametrize("prefix", ["\t", "  ", "    ", "        "])
def test_python_block_indentation_becomes_four_spaces_with_identical_ast(prefix):
    import ast

    source = ("def solve():\n" + prefix + "value = 0\n" + prefix + "for i in range(4):\n"
              + prefix * 2 + "value += i\n" + prefix + "return value\n")
    expected = "def solve():\n    value = 0\n    for i in range(4):\n        value += i\n    return value\n"
    result = code_tabs(source)
    assert result == expected
    assert ast.dump(ast.parse(result)) == ast.dump(ast.parse(source))
    namespace = {}
    exec(compile(result, "synthetic-style", "exec"), namespace)  # noqa: S102 — fixed test literal.
    assert namespace["solve"]() == 6


def test_python_multiline_literals_and_continuations_are_preserved():
    source = ('def solve():\n\tvalue = """line\n\tkeep literal tab\n    keep spaces\n"""\n'
              '\tresult = (\n\t\tvalue,\n\t\t1 + 2,\n\t)\n\treturn result\n')
    result = code_tabs(source)
    assert '\n\tkeep literal tab\n    keep spaces\n' in result
    assert '\n    result = (\n        value,\n        1 + 2,\n    )' in result
    before, after = {}, {}
    exec(compile(source, "before", "exec"), before)  # noqa: S102 — fixed test literal.
    exec(compile(result, "after", "exec"), after)  # noqa: S102 — fixed test literal.
    assert before["solve"]() == after["solve"]()


@pytest.mark.parametrize("source", [
    'def f(x):\n\treturn f"""literal\n\t{x}\n"""\n',
    'def f():\n\treturn r"""literal\n\tkeep tab\n"""\n',
])
def test_python_fstrings_and_raw_strings_keep_their_values(source):
    import ast

    result = code_tabs(source)
    assert ast.dump(ast.parse(source)) == ast.dump(ast.parse(result))
    assert result.startswith("def f") and "\n    return " in result


def test_invalid_mixed_python_indentation_is_not_guessed():
    source = "def f():\n\tvalue = 1\n    return value\n"
    assert code_tabs(source) == source


def test_formatter_failure_cannot_corrupt_model_output(monkeypatch):
    from unittest.mock import Mock

    source = "int main() {\n    return 0;\n}\n"
    monkeypatch.setattr("ucpc.code_format._indent", Mock(side_effect=RuntimeError("lexer failure")))
    assert code_tabs(source) == source


def test_python_import_followed_by_from_is_not_misidentified_as_javascript():
    source = "import sys\nfrom collections import deque\n\nwhile True:\n\tbreak\n"
    assert code_tabs(source) == "import sys\nfrom collections import deque\n\nwhile True:\n    break\n"


@pytest.mark.stress
def test_one_thousand_python_literal_and_indentation_combinations_keep_identical_ast():
    import ast
    import random

    rng = random.Random(1904)
    for _ in range(1000):
        unit = rng.choice(["\t", "  ", "    ", "        "])
        value = rng.randrange(-100000, 100000)
        source = ("import sys\nfrom collections import deque\n\ndef solve():\n"
                  + unit + f"value = {value}\n" + unit + 'text = """Привіт 👋\n'
                  + unit + "literal indentation must stay unchanged\n" + '"""\n'
                  + unit + "for i in range(5):\n" + unit * 2 + "value += i\n"
                  + unit + "return value, text\n")
        result = code_tabs(source)
        assert ast.dump(ast.parse(result)) == ast.dump(ast.parse(source))
        assert "\n    value = " in result and "\n        value += i" in result
        assert unit + "literal indentation must stay unchanged" in result
