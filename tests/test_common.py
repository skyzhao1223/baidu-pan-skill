"""Offline tests for bdpan_common: URL parsing, page parsing, cookie loading."""

import json

import pytest
from bdpan_common import (
    check_cookies,
    human_size,
    load_cookies,
    parse_locals_mset,
    parse_share_url,
)


@pytest.mark.parametrize(
    "url,surl,pwd",
    [
        ("https://pan.baidu.com/s/11PMFpFl7lVw3Q3I0yvm9pQ?pwd=cbwv",
         "1PMFpFl7lVw3Q3I0yvm9pQ", "cbwv"),
        ("https://pan.baidu.com/s/1abcDEF_123-xy", "abcDEF_123-xy", None),
        ("分享来自百度网盘: https://pan.baidu.com/s/1zzzz?pwd=9x8y 提取码: 9x8y",
         "zzzz", "9x8y"),
    ],
)
def test_parse_share_url(url, surl, pwd):
    assert parse_share_url(url) == (surl, pwd)


def test_parse_share_url_rejects_non_share():
    with pytest.raises(ValueError):
        parse_share_url("https://example.com/s/1abc")


FIXTURE_PAGE = """<html><script>
locals.mset({"csrf":"x","shareid":47024652848,"share_uk":"2857161019",
"linkusername":"ia**sk","expiredType":544389,
"file_list":[{"fs_id":710626593200896,"server_filename":"a.mov","size":712957982,
"isdir":0,"path":"/share/a.mov","md5":"458a09d08pdf","duration":3505,
"category":1},{"fs_id":1041572308954583,"server_filename":"b.mov","size":740378697,
"isdir":0,"path":"/录屏/b.mov","md5":"200833933peb","duration":3615}]});
</script></html>"""


def test_parse_locals_mset():
    data = parse_locals_mset(FIXTURE_PAGE)
    assert data is not None
    assert data["shareid"] == 47024652848
    assert data["share_uk"] == "2857161019"
    assert len(data["file_list"]) == 2
    f0 = data["file_list"][0]
    assert f0["fs_id"] == 710626593200896
    assert f0["duration"] == 3505


def test_parse_locals_mset_missing():
    assert parse_locals_mset("<html>no data here</html>") is None


def test_load_cookies_filters_and_drops_non_ascii(tmp_path):
    p = tmp_path / "cookies.json"
    p.write_text(json.dumps({
        "BDUSS": "good-ascii",
        "STOKEN": "st",
        "WEIRD": "bad\ufffd\ufffdvalue",
        "NUM": 42,
        "SOME_TRACKER": "not-on-whitelist",  # 100+ cookie jars trip nginx 400
    }), encoding="utf-8")
    ck = load_cookies(p)
    assert ck == {"BDUSS": "good-ascii", "STOKEN": "st"}


def test_check_cookies():
    assert check_cookies({"BDUSS": "a", "STOKEN": "b", "BAIDUID": "c"}) == []
    assert check_cookies({"BDUSS": "a"}) == ["STOKEN", "BAIDUID"]


def test_human_size():
    assert human_size(512) == "512B"
    assert human_size(712957982) == "679.9MB"
    assert human_size(2 * 1024**3) == "2.0GB"
