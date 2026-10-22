"""MCP servers for AMD Vivado and Vitis, joined by an XSA hand-off.

amd_mcp.vivado_server  vivado-mcp-native's tools, plus export_hardware (writes the XSA and
                       records it in the hand-off) and longer, settable timeouts
amd_mcp.vitis_server   Vitis platforms, domains and applications through a persistent Vitis
                       Python worker, and JTAG runs through a persistent XSDB session; the
                       XSA defaults to the one Vivado exported last
"""

__version__ = "0.1.0"
