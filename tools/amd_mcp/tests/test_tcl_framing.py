"""The XSDB wrapper evaluated by a real Tcl 8.6 interpreter (tkinter's), with puts captured."""

import uuid

import pytest

from amd_mcp.tcl_session import TclSession

tkinter = pytest.importorskip("tkinter")


def run_wrapped(command: str):
    interp = tkinter.Tcl()
    if float(interp.eval("info tclversion")) < 8.6:
        pytest.skip("needs Tcl 8.6")
    printed = []

    def puts(*args):
        args = [a for a in args if a != "-nonewline"]
        printed.append(str(args[-1]) + "\n")
    interp.createcommand("puts", puts)
    interp.createcommand("flush", lambda *a: None)
    token = uuid.uuid4().hex
    script, m = TclSession._wrapper(command, token)
    interp.eval(script)
    text = "".join(printed)
    begin = text.index(m["begin"]) + len(m["begin"])
    end = text.index(m["end"])
    return TclSession.parse_reply(text[begin:end], m)


def test_result_and_output():
    r = run_wrapped('puts "hello"; expr {6*7}')
    assert r.ok and r.result == "42" and r.output.strip() == "hello"


def test_special_characters_survive():
    value = 'a "quoted" {brace} \\backslash $dollar [bracket] ; semi\nnewline 中文'
    r = run_wrapped("set v " + "{" + value + "}")
    assert r.ok and r.result == value


def test_error_has_stack():
    r = run_wrapped("proc boom {} {error {deliberate}}\nboom")
    assert not r.ok and r.result == "deliberate"
    assert "boom" in r.error


def test_global_scope():
    interp_result = run_wrapped("set ::g 5; proc p {} {return $::g}; p")
    assert interp_result.ok and interp_result.result == "5"


def test_return_code_is_ok():
    assert run_wrapped("return 7").ok
