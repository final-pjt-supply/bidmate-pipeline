# -*- coding: utf-8 -*-
"""hwp_extractor의 hwp5proc 실패 처리 단위테스트.

실제 hwp5proc를 부르지 않고 subprocess.run을 monkeypatch로 대체한다 — 검증
대상은 "hwp5proc가 이렇게 끝났을 때 우리 코드가 무엇을 하는가"지, hwp5proc
자체가 아니다.

배경(실측): DLQ에 쌓인 HWP 8건을 조사할 때, 로그에 남는 건 CalledProcessError의
"returned non-zero exit status 1"뿐이라 원인을 알 수 없어 실패 문서를 매번
S3에서 내려받아 재현해야 했다. 아래 테스트는 그 사유(stderr)가 실제로 예외에
실려 나오는지, 그리고 중단 직전까지의 출력이 버려지지 않는지를 고정한다.
"""
import subprocess

import pytest

from parsing.hwp_hwpx import hwp_extractor
from parsing.hwp_hwpx.hwp_extractor import Hwp5procError

# XML 1.0이 허용하지 않는 문자들 — 실측으로 hwp5proc 출력의 DocInfo에서 발견됨
ILLEGAL = chr(0x02) + chr(0x11) + chr(0xFFFF)

FULL_XML = b"<HwpDoc><BodyText><Paragraph><Text>\xea\xb3\xb5\xea\xb3\xa0</Text></Paragraph></BodyText></HwpDoc>"
# 끝이 잘려 닫는 태그가 없는 XML (hwp5proc가 중간에 죽었을 때의 stdout 모양)
TRUNCATED_XML = b"<HwpDoc><BodyText><Paragraph><Text>\xea\xb3\xb5\xea\xb3\xa0</Text></Paragraph>"


class _Completed:
    def __init__(self, returncode, stdout=b"", stderr=b""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _fake_run(monkeypatch, returncode, stdout=b"", stderr=b""):
    monkeypatch.setattr(
        hwp_extractor.subprocess, "run",
        lambda *a, **kw: _Completed(returncode, stdout, stderr),
    )


def test_실패시_stderr가_예외에_실린다(monkeypatch):
    _fake_run(monkeypatch, 1, stderr="ERROR Not an OLE2 Compound Binary File.".encode())
    with pytest.raises(Hwp5procError) as ei:
        hwp_extractor._generate_xml("x.hwp")
    assert "Not an OLE2" in str(ei.value)
    assert "Not an OLE2" in ei.value.stderr


def test_stderr의_ANSI_컬러코드는_제거된다(monkeypatch):
    colored = ("\x1b[31mERROR\x1b[0m AssertionError").encode()
    _fake_run(monkeypatch, 1, stderr=colored)
    with pytest.raises(Hwp5procError) as ei:
        hwp_extractor._generate_xml("x.hwp")
    assert "\x1b[" not in ei.value.stderr
    assert "AssertionError" in ei.value.stderr


def test_중단직전_출력은_예외에_보존된다(monkeypatch):
    _fake_run(monkeypatch, 1, stdout=TRUNCATED_XML, stderr=b"AssertionError")
    with pytest.raises(Hwp5procError) as ei:
        hwp_extractor._generate_xml("x.hwp")
    assert ei.value.stdout == TRUNCATED_XML


def test_정상종료면_stdout을_그대로_돌려준다(monkeypatch):
    _fake_run(monkeypatch, 0, stdout=FULL_XML)
    assert hwp_extractor._generate_xml("x.hwp") == FULL_XML


def test_중간에_죽어도_부분추출로_살린다(monkeypatch):
    """stdout이 남아 있으면 통째로 버리지 않고 recover 파싱 후 partial 표시."""
    _fake_run(monkeypatch, 1, stdout=TRUNCATED_XML, stderr=b"AssertionError")
    root, partial = hwp_extractor._parse_xml("x.hwp")
    assert partial is True
    assert root.find(".//Text").text == "공고"


def test_출력이_아예_없으면_예외를_그대로_올린다(monkeypatch):
    """OLE가 아닌 파일 등 — 살릴 게 없으므로 부분추출로 위장하지 않는다."""
    _fake_run(monkeypatch, 1, stdout=b"", stderr=b"Not an OLE2 Compound Binary File.")
    with pytest.raises(Hwp5procError):
        hwp_extractor._parse_xml("x.hwp")


def test_XML_비허용_문자가_섞여도_파싱한다(monkeypatch):
    """hwp5proc가 exit 0으로 끝나도 lxml이 거부하는 케이스(실측 R26BK01650092).

    본문이 아니라 DocInfo의 Style name 등에 섞이므로 제거해도 본문은 온전하다.
    """
    dirty = (
        '<HwpDoc><Style name="' + ILLEGAL + '"/>'
        "<BodyText><Paragraph><Text>공고</Text></Paragraph></BodyText></HwpDoc>"
    ).encode("utf-8")
    _fake_run(monkeypatch, 0, stdout=dirty)
    root, partial = hwp_extractor._parse_xml("x.hwp")
    assert partial is False          # 잘린 게 아니라 온전한 문서다
    assert root.find(".//Text").text == "공고"


def test_정상문서는_partial이_붙지_않는다(monkeypatch):
    _fake_run(monkeypatch, 0, stdout=FULL_XML)
    root, partial = hwp_extractor._parse_xml("x.hwp")
    assert partial is False
    assert root.find(".//Text").text == "공고"


def test_strip_illegal_xml은_탭_개행을_남긴다():
    keep = "가" + chr(0x09) + chr(0x0A) + chr(0x0D) + "나"
    out = hwp_extractor._strip_illegal_xml((keep + ILLEGAL).encode("utf-8")).decode("utf-8")
    assert out == keep


def test_실제_subprocess_계약_확인():
    """monkeypatch가 감춘 가정(capture_output 없이도 stdout/stderr가 잡히는가)을
    실제 subprocess로 한 번 확인한다 — 여기가 어긋나면 위 테스트가 전부 헛돈다."""
    proc = subprocess.run(
        [__import__("sys").executable, "-c",
         "import sys; sys.stdout.write('OUT'); sys.stderr.write('ERR'); sys.exit(1)"],
        capture_output=True,
    )
    assert (proc.returncode, proc.stdout, proc.stderr) == (1, b"OUT", b"ERR")
