from parser import parse_pair


def test_unicode_pair() -> None:
    assert parse_pair("名字:值") == ("名字", "值")
