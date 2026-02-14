"""Tests for the C/C++ source annotation parser."""

from shifting_codes.ui.source_parser import parse_annotations


def test_parse_no_annotations():
    """Plain function without annotation is not marked."""
    source = """\
int add(int a, int b) {
    return a + b;
}
"""
    result = parse_annotations(source)
    assert len(result) == 1
    assert result[0].name == "add"
    assert result[0].annotated is False


def test_parse_with_annotation():
    """// @obfuscate above one of two functions marks only that one."""
    source = """\
int plain(int x) {
    return x;
}

// @obfuscate
int secret(int x) {
    return x ^ 0x42;
}
"""
    result = parse_annotations(source)
    names = {f.name: f.annotated for f in result}
    assert names["plain"] is False
    assert names["secret"] is True


def test_block_comment_annotation():
    """/* @obfuscate */ style is also recognized."""
    source = """\
/* @obfuscate */
void encrypt(char* buf) {
    buf[0] ^= 0xFF;
}
"""
    result = parse_annotations(source)
    assert len(result) == 1
    assert result[0].name == "encrypt"
    assert result[0].annotated is True


def test_keywords_filtered():
    """Control flow keywords are not mistaken for function definitions."""
    source = """\
int compute(int x) {
    if (x > 0) {
        return x;
    }
    while (x < 10) {
        x++;
    }
    for (int i = 0; i < x; i++) {
    }
    return x;
}
"""
    result = parse_annotations(source)
    # Only 'compute' should be found, not 'if', 'while', or 'for'
    assert len(result) == 1
    assert result[0].name == "compute"


def test_annotation_gap():
    """Annotation 2-3 lines above function still matches."""
    source = """\
// @obfuscate

// This function does important work
int important(int x) {
    return x * 2;
}
"""
    result = parse_annotations(source)
    assert len(result) == 1
    assert result[0].name == "important"
    assert result[0].annotated is True


def test_annotation_too_far():
    """Annotation more than 3 lines above function does NOT match."""
    source = """\
// @obfuscate




int far_away(int x) {
    return x;
}
"""
    result = parse_annotations(source)
    assert len(result) == 1
    assert result[0].name == "far_away"
    assert result[0].annotated is False


def test_multiple_functions():
    """Multiple functions with mixed annotations."""
    source = """\
int first(int a) {
    return a;
}

// @obfuscate
int second(int b) {
    return b;
}

int third(int c) {
    return c;
}

/* @obfuscate */
int fourth(int d) {
    return d;
}
"""
    result = parse_annotations(source)
    names = {f.name: f.annotated for f in result}
    assert len(names) == 4
    assert names["first"] is False
    assert names["second"] is True
    assert names["third"] is False
    assert names["fourth"] is True


def test_static_function():
    """Static functions are also recognized."""
    source = """\
// @obfuscate
static int helper(int x) {
    return x + 1;
}
"""
    result = parse_annotations(source)
    assert len(result) == 1
    assert result[0].name == "helper"
    assert result[0].annotated is True
