from src.main import main


def test_main_is_importable_without_a_display():
    assert callable(main)
