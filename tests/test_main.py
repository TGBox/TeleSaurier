from src.main import main


def test_main(capsys):
    main()
    captured = capsys.readouterr()
    assert "TeleSaurier" in captured.out