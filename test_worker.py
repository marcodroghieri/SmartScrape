from worker import pulisci_prezzo, pulisci_recensioni, pulisci_stelle


def test_prezzo_formato_italiano():
    assert pulisci_prezzo("1.299,00 €") == 1299.0


def test_prezzo_intervallo_tiene_il_primo():
    assert pulisci_prezzo("199 - 249") == 199.0


def test_prezzo_mancante():
    assert pulisci_prezzo(None) is None


def test_stelle_con_virgola():
    assert pulisci_stelle("4,5") == 4.5


def test_stelle_non_numeriche():
    assert pulisci_stelle("n/d") == 0.0


def test_recensioni_con_k():
    assert pulisci_recensioni("1,2K") == 1200


def test_recensioni_con_piu():
    assert pulisci_recensioni("350+") == 350
