from teatar.textnorm import matches, month_number, normalize, to_latin


def test_cyrillic_latin_caps_are_equal():
    assert normalize("Дивље месо") == normalize("Divlje meso") == normalize("DIVLjE MESO") == "divlje meso"
    assert normalize("Ричард Други") == normalize("Ričard Drugi") == "ricard drugi"
    assert normalize("Ђорђе") == normalize("Đorđe") == "djordje"


def test_to_latin_keeps_case():
    assert to_latin("Љубавнице") == "Ljubavnice"
    assert to_latin("Сцена „Раша Плаовић”") == "Scena „Raša Plaović”"


def test_months():
    assert month_number("септембар") == 9
    assert month_number("окт") == 10
    assert month_number("DECEMBAR") == 12
    assert month_number("avgusta") == 8
    assert month_number("xyz") is None


def test_matches():
    assert matches("divlje", "Дивље месо")
    assert matches("meso divlje", "DIVLjE MESO")
    assert not matches("tramvaj", "Дивље месо")
    assert not matches("", "anything")
