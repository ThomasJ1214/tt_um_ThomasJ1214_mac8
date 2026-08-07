# SPDX-FileCopyrightText: (c) 2026 Thomas Jenkins
# SPDX-License-Identifier: Apache-2.0
#
# Testbench for the 8x8 signed multiply-accumulate unit.
#
# Everything here is black-box: the tests only ever drive the real chip pins and
# read the real chip pins. Nothing reaches inside the module to peek at a
# register. That matters because the same tests are re-run against the
# gate-level netlist after hardening, and by then the internal register names
# are gone, so a test that peeked inside would break.

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge, Timer

# A deliberately slow clock, 10 us per cycle, which is 100 kHz.
#
# This looks far slower than the hardware needs, and for the RTL simulation it
# is. The reason is the gate-level run. After the design is hardened, these same
# tests are re-run against the synthesised netlist, which the Makefile compiles
# with UNIT_DELAY=#1, giving every single gate one nanosecond of delay. The
# multiply-and-add path is roughly 25 to 35 gates deep, so it needs tens of
# nanoseconds to settle in that mode. A clock in that range would latch garbage
# and the gate-level test would fail even though the silicon is fine. 10 us
# leaves room for any realistic logic depth.
#
# The real timing target is unaffected by this. That is set by CLOCK_PERIOD in
# src/config.json, which is what static timing analysis signs off against.
CLK_PERIOD_US = 10

# After a clock edge the flip-flops update, then the combinational output mux
# settles a moment later. Waiting before sampling uo_out avoids reading the
# value from before the edge, and it is generous for the same gate-delay reason.
SETTLE_US = 1

# Opcodes, driven on uio_in[2:0]. These mirror the localparams in the Verilog.
OP_NOP = 0b000
OP_LOAD_A = 0b001
OP_LOAD_B = 0b010
OP_MAC = 0b011
OP_CLEAR = 0b100


def ctrl(opcode, byte_sel=0):
    """Pack an opcode and a byte-select into the uio_in bus layout."""
    return (byte_sel << 3) | opcode


def u8(value):
    """Python int -> 8-bit two's complement, the way the data bus carries it."""
    return value & 0xFF


def u32(value):
    """Python int -> 32-bit two's complement, matching the accumulator."""
    return value & 0xFFFFFFFF


async def setup(dut):
    """Start the clock and put the design through a clean reset.

    Every test calls this and gets its own clock. cocotb shuts down any task a
    test started once that test finishes, so a clock launched in test 1 is gone
    by test 2. Each test has to start its own or it will sit waiting on an
    edge that never arrives.
    """
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_US, unit="us").start())

    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = ctrl(OP_NOP)
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)
    await Timer(SETTLE_US, unit="us")


async def step(dut, opcode, data=0):
    """Present one instruction and let the next rising edge execute it."""
    dut.ui_in.value = u8(data)
    dut.uio_in.value = ctrl(opcode)
    await RisingEdge(dut.clk)
    await Timer(SETTLE_US, unit="us")


async def read_acc(dut):
    """Read all four accumulator bytes back through the 8-bit output port.

    Deliberately no clock edges in here. The byte-select mux is combinational,
    so walking the select lines is enough to see all 32 bits. The opcode is held
    at NOP throughout, so the free-running clock cannot disturb the value
    mid-read.
    """
    value = 0
    for sel in range(4):
        dut.uio_in.value = ctrl(OP_NOP, sel)
        await Timer(SETTLE_US, unit="us")
        value |= int(dut.uo_out.value) << (8 * sel)
    return value


@cocotb.test()
async def test_01_positive_multiply(dut):
    """3 * 4 = 12, starting from a cleared accumulator."""
    await setup(dut)
    await step(dut, OP_CLEAR)
    await step(dut, OP_LOAD_A, 3)
    await step(dut, OP_LOAD_B, 4)
    await step(dut, OP_MAC)

    got = await read_acc(dut)
    assert got == 12, f"expected 12, got {got}"


@cocotb.test()
async def test_02_accumulates(dut):
    """A second MAC adds on top of the first: 12 + 30 = 42."""
    await setup(dut)
    await step(dut, OP_CLEAR)
    await step(dut, OP_LOAD_A, 3)
    await step(dut, OP_LOAD_B, 4)
    await step(dut, OP_MAC)
    assert await read_acc(dut) == 12, "first MAC did not land"

    await step(dut, OP_LOAD_A, 5)
    await step(dut, OP_LOAD_B, 6)
    await step(dut, OP_MAC)

    got = await read_acc(dut)
    assert got == 42, f"expected 42, got {got}"


@cocotb.test()
async def test_03_negative_times_positive(dut):
    """-3 * 4 = -12, which reads back as 0xFFFFFFF4."""
    await setup(dut)
    await step(dut, OP_CLEAR)
    await step(dut, OP_LOAD_A, u8(-3))
    await step(dut, OP_LOAD_B, 4)
    await step(dut, OP_MAC)

    got = await read_acc(dut)
    assert got == u32(-12), f"expected 0x{u32(-12):08X}, got 0x{got:08X}"


@cocotb.test()
async def test_04_negative_times_negative(dut):
    """-3 * -4 = +12. Two negatives must come back positive."""
    await setup(dut)
    await step(dut, OP_CLEAR)
    await step(dut, OP_LOAD_A, u8(-3))
    await step(dut, OP_LOAD_B, u8(-4))
    await step(dut, OP_MAC)

    got = await read_acc(dut)
    assert got == 12, f"expected 12, got 0x{got:08X}"


@cocotb.test()
async def test_05_most_negative_squared(dut):
    """-128 * -128 = +16384. This is the one that catches a too-narrow product.

    +16384 is 2**14, so it is the only 8x8 signed product that needs the full
    16th bit. Size the internal product at 15 bits and this silently comes back
    as -16384 instead.
    """
    await setup(dut)
    await step(dut, OP_CLEAR)
    await step(dut, OP_LOAD_A, u8(-128))
    await step(dut, OP_LOAD_B, u8(-128))
    await step(dut, OP_MAC)

    got = await read_acc(dut)
    assert got == 16384, f"expected 16384, got 0x{got:08X} ({got - 2**32})"


@cocotb.test()
async def test_06_most_positive_squared(dut):
    """127 * 127 = 16129, the largest positive product."""
    await setup(dut)
    await step(dut, OP_CLEAR)
    await step(dut, OP_LOAD_A, 127)
    await step(dut, OP_LOAD_B, 127)
    await step(dut, OP_MAC)

    got = await read_acc(dut)
    assert got == 16129, f"expected 16129, got 0x{got:08X}"


@cocotb.test()
async def test_07_byte_select_sweep(dut):
    """Every one of the four byte lanes reads back the right slice of acc.

    The accumulator is driven past 2**24 first, so all four bytes hold a
    different non-zero value and a mux that swapped two lanes would be caught.
    """
    await setup(dut)
    await step(dut, OP_CLEAR)
    await step(dut, OP_LOAD_A, 127)
    await step(dut, OP_LOAD_B, 127)

    reps = 1100  # 1100 * 16129 = 17,741,900 == 0x010EB84C, four distinct bytes
    for _ in range(reps):
        await step(dut, OP_MAC)
    expected = u32(127 * 127 * reps)

    # No clock edges below: changing the byte select alone has to be enough.
    for sel in range(4):
        dut.uio_in.value = ctrl(OP_NOP, sel)
        await Timer(SETTLE_US, unit="us")
        got = int(dut.uo_out.value)
        want = (expected >> (8 * sel)) & 0xFF
        assert got == want, f"byte {sel}: expected 0x{want:02X}, got 0x{got:02X}"


@cocotb.test()
async def test_08_reset_clears_all_state(dut):
    """Reset asserted mid-sequence wipes acc, a_reg and b_reg.

    a_reg and b_reg have no pins of their own, so they are checked through the
    datapath: multiply by a known non-zero operand and confirm the answer is
    still zero, which is only possible if the other operand really is zero.
    """
    await setup(dut)
    await step(dut, OP_CLEAR)
    await step(dut, OP_LOAD_A, 7)
    await step(dut, OP_LOAD_B, 9)
    await step(dut, OP_MAC)
    assert await read_acc(dut) == 63, "setup failed, state was never made dirty"

    # Yank reset low partway through.
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 3)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)
    await Timer(SETTLE_US, unit="us")

    got = await read_acc(dut)
    assert got == 0, f"acc survived reset: 0x{got:08X}"

    # b_reg == 0? Load a known non-zero A and multiply. 100 * b_reg must be 0.
    await step(dut, OP_LOAD_A, 100)
    await step(dut, OP_MAC)
    got = await read_acc(dut)
    assert got == 0, f"b_reg survived reset, 100 * b_reg = 0x{got:08X}"

    # a_reg == 0? Reset again so A is clean, then multiply by a known non-zero B.
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 3)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)
    await Timer(SETTLE_US, unit="us")

    await step(dut, OP_LOAD_B, 100)
    await step(dut, OP_MAC)
    got = await read_acc(dut)
    assert got == 0, f"a_reg survived reset, a_reg * 100 = 0x{got:08X}"


@cocotb.test()
async def test_09_reserved_opcodes_do_nothing(dut):
    """Opcodes 101, 110 and 111 are undefined and must behave like NOP."""
    await setup(dut)
    await step(dut, OP_CLEAR)
    await step(dut, OP_LOAD_A, 5)
    await step(dut, OP_LOAD_B, 6)
    await step(dut, OP_MAC)
    assert await read_acc(dut) == 30, "setup failed"

    # Hold a disruptive value on the data bus the whole time, so an opcode that
    # accidentally decoded as LOAD_A or LOAD_B would corrupt an operand.
    for bad in (0b101, 0b110, 0b111):
        await step(dut, bad, 0x7F)
        got = await read_acc(dut)
        assert got == 30, f"opcode {bad:03b} changed acc to 0x{got:08X}"

    # a_reg and b_reg must also be untouched, so one more MAC adds 5*6 again.
    await step(dut, OP_MAC)
    got = await read_acc(dut)
    assert got == 60, f"reserved opcodes corrupted an operand: got {got}, want 60"


# The nine tests above are the required set. The three below close gaps that
# reading the specification carefully turns up.


@cocotb.test()
async def test_10_clear_preserves_operands(dut):
    """CLEAR zeroes the accumulator and leaves A and B untouched.

    CLEAR is specified as acc <= 0 and nothing else, so the same operands should
    still be loaded afterwards and a single MAC should reproduce the old total.
    """
    await setup(dut)
    await step(dut, OP_CLEAR)
    await step(dut, OP_LOAD_A, 11)
    await step(dut, OP_LOAD_B, 7)
    await step(dut, OP_MAC)
    assert await read_acc(dut) == 77, "setup failed"

    await step(dut, OP_CLEAR)
    assert await read_acc(dut) == 0, "CLEAR did not zero the accumulator"

    await step(dut, OP_MAC)
    got = await read_acc(dut)
    assert got == 77, f"CLEAR disturbed an operand: got {got}, want 77"


@cocotb.test()
async def test_11_accumulate_through_zero(dut):
    """A running total can go negative and come back without losing its sign."""
    await setup(dut)
    await step(dut, OP_CLEAR)

    # Three lots of -100 take the total down to -300.
    await step(dut, OP_LOAD_A, u8(-100))
    await step(dut, OP_LOAD_B, 1)
    for _ in range(3):
        await step(dut, OP_MAC)
    got = await read_acc(dut)
    assert got == u32(-300), f"expected -300, got 0x{got:08X}"

    # Four lots of +100 cross back through zero and land on +100.
    await step(dut, OP_LOAD_A, 100)
    for _ in range(4):
        await step(dut, OP_MAC)
    got = await read_acc(dut)
    assert got == 100, f"expected +100 after crossing zero, got 0x{got:08X}"


@cocotb.test()
async def test_12_large_negative_byte_lanes(dut):
    """The byte lanes read back a large negative total correctly.

    Test 7 sweeps the lanes with a positive value. This drives the accumulator
    past -2**24 so the upper lanes carry sign bits rather than zeros.
    """
    await setup(dut)
    await step(dut, OP_CLEAR)
    await step(dut, OP_LOAD_A, u8(-128))
    await step(dut, OP_LOAD_B, 127)

    reps = 1040  # 1040 * -16256 = -16,906,240, which is past -2**24
    for _ in range(reps):
        await step(dut, OP_MAC)

    expected = u32(-128 * 127 * reps)
    got = await read_acc(dut)
    assert got == expected, f"expected 0x{expected:08X}, got 0x{got:08X}"
