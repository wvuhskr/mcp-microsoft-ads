from mcp_microsoft_ads.tools import geo_read

CSV = """Location Id,Bing Display Name,Location Type,Status
190,"Example State, United States",State,Active
81237,"Springfield, Example State, United States",City,Active
81238,"Springfield, Sample State, United States",City,Active
"""

def test_search_matches_case_insensitive():
    rows = geo_read._search_rows(CSV, "springfield, example state")
    assert len(rows) == 1
    assert rows[0] == {"id": 81237, "name": "Springfield, Example State, United States",
                       "type": "City", "status": "Active"}

def test_search_multiple():
    assert len(geo_read._search_rows(CSV, "springfield")) == 2

# live-probed: real geo file uses "|" as the Bing Display Name separator, not ", ".
PIPE_CSV = """Location Id,Bing Display Name,Location Type,Replaces,Status,AdWords Location Id
45163,Springfield|Example State|United States,City,,Active,1015088
45741,Springfield|Sample State|United States,City,,Active,1015406
"""

def test_search_matches_pipe_delimited_live_format():
    rows = geo_read._search_rows(PIPE_CSV, "springfield, example state")
    assert len(rows) == 1
    assert rows[0] == {"id": 45163, "name": "Springfield, Example State, United States",
                       "type": "City", "status": "Active"}
