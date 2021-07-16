import pytest

from aurweb.templates import register_filter


@register_filter("func")
def func(): pass


def test_register_filter_exists_key_error():
    """ Most instances of register_filter are tested through module
    imports or template renders, so we only test failures here. """
    with pytest.raises(KeyError):
        @register_filter("func")
        def some_func(): pass
