from worker import pulisci_prezzo


def test_prezzo_formato_italiano():
    assert pulisci_prezzo("1.299,00 €") == 1299.0