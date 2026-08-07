![](../../workflows/gds/badge.svg) ![](../../workflows/docs/badge.svg) ![](../../workflows/test/badge.svg) ![](../../workflows/fpga/badge.svg)

# 8x8 signed MAC unit

An 8-bit by 8-bit signed multiply-accumulate unit with a 32-bit accumulator, built for the Tiny Tapeout
TTSKY26c shuttle on the sky130A process.

- [Project datasheet](docs/info.md)

## What it does

The whole design exists to run one operation:

```
acc = acc + (A * B)
```

A and B are 8-bit signed values, so each holds a whole number between -128 and +127. The accumulator is 32 bits
wide and also signed.

That operation is the multiply-accumulate, usually shortened to MAC. It is the arithmetic underneath matrix
multiplication, which is the arithmetic underneath neural networks. A production accelerator tiles hundreds or
thousands of these units into a grid called a systolic array, where each unit hands its result to its neighbour.
This project is a single unit from such a grid, isolated so that its behaviour is visible on a bench.

## Block diagram

```
                          ui_in[7:0]   (operand data bus)
                               |
              +----------------+----------------+
              |                                 |
        +-----v------+                    +-----v------+
        |   a_reg    |                    |   b_reg    |
        |  8-bit     |                    |  8-bit     |
        |  signed    |                    |  signed    |
        +-----+------+                    +-----+------+
              |                                 |
              +--------+               +--------+
                       |               |
                 +-----v---------------v-----+
                 |    signed 8 x 8 multiply  |
                 +-------------+-------------+
                               |
                               | 16-bit signed product
                               | (sign-extended to 32)
                         +-----v-----+
                         |     +     | <-------------+
                         +-----+-----+               |
                               |                     |
                    +----------v-----------+         |
                    |   acc, 32-bit signed |---------+
                    +----------+-----------+
                               |
                               | 32 bits
                    +----------v-----------+
                    |  byte-select mux     | <---- uio_in[4:3]
                    |  (combinational)     |
                    +----------+-----------+
                               |
                               | 8 bits
                          uo_out[7:0]


   uio_in[2:0] ----> opcode decoder ----> write enables for a_reg, b_reg, acc
```

Everything in the left and centre of that diagram updates on the rising edge of the clock. The byte-select mux
at the bottom is the only combinational path to the outputs.

## Interface

| Signal        | Direction | Purpose                                     |
|---------------|-----------|---------------------------------------------|
| `ui_in[7:0]`  | in        | Operand data bus                            |
| `uio_in[2:0]` | in        | Opcode                                      |
| `uio_in[4:3]` | in        | Accumulator byte select                     |
| `uio_in[7:5]` | in        | Unused                                      |
| `uo_out[7:0]` | out       | Selected byte of the accumulator            |
| `uio_out`     | out       | Tied to `8'h00`                             |
| `uio_oe`      | out       | Tied to `8'h00`, so every `uio` pin is an input |
| `rst_n`       | in        | Reset, active low, synchronous              |

### Opcodes, on `uio_in[2:0]`

| Value | Name   | Effect                                        |
|-------|--------|-----------------------------------------------|
| 000   | NOP    | No state changes                              |
| 001   | LOAD_A | `a_reg <= ui_in`                              |
| 010   | LOAD_B | `b_reg <= ui_in`                              |
| 011   | MAC    | `acc <= acc + (a_reg * b_reg)`, signed        |
| 100   | CLEAR  | `acc <= 0`                                    |
| 101, 110, 111 | (unassigned) | Behave as NOP                    |

### Byte select, on `uio_in[4:3]`

| Value | `uo_out` shows |
|-------|----------------|
| 00    | `acc[7:0]`     |
| 01    | `acc[15:8]`    |
| 10    | `acc[23:16]`   |
| 11    | `acc[31:24]`   |

## Why the pins are arranged this way

Tiny Tapeout gives every project the same fixed pinout: 8 dedicated inputs, 8 dedicated outputs, and 8
bidirectional pins. The design has to fit inside that budget, and this one has an obvious tension in it. The
accumulator is 32 bits wide and there are only 8 output pins.

Reading the accumulator out one byte at a time is the way around that. It costs 2 input bits for a byte
selector and no extra output pins, and it means a result of any width can be read through the same 8 pins.
Reading is also free in the sense that it needs no clock edge, so it cannot disturb the value being read.

Given that, the rest of the allocation follows:

The operands take the whole dedicated input port. An 8-bit signed operand needs all 8 bits to cover -128 to
+127, so there is no room to borrow a bit or two from `ui_in` for control. Keeping the data bus contiguous also
means the demo board's bank of 8 switches maps directly onto one number, instead of a number with control bits
scattered through it.

Control therefore lives on the bidirectional port. The design never drives anything outwards, so `uio_oe` is
tied to zero and all 8 bidirectional pins act as plain inputs. Five are used: 3 for the opcode and 2 for the
byte selector. The remaining 3 are left spare rather than invented uses for.

Two alternatives were considered and rejected:

Putting the byte selector on `ui_in[7:6]` and shrinking the operand bus to 6 bits would free up bidirectional
pins, but 6-bit operands only span -32 to +31. The design is specified for the full 8-bit signed range, so this
was not an option.

Replacing the 2-bit selector with a pointer that advances automatically on every read would save one input pin.
It was rejected because it adds a register, and because reads then depend on how many reads came before, which
is awkward to drive by hand from switches and awkward to reason about when a test fails.

## The part worth understanding

The internal product of A and B is 16 bits wide, and that width is deliberate.

Fifteen bits is enough for every product these operands can produce except exactly one. The largest magnitude
comes from squaring the most negative input:

```
-128 * -128 = +16384 = 2^14
```

A 15-bit signed number runs from -16384 to +16383, so +16384 lands directly on the sign bit and reads back as
-16384. The answer is wrong by 32768 and nothing warns you, because as far as the language is concerned nothing
illegal happened. Sixteen bits, which is simply the width of A plus the width of B, has room for it.

The second trap is signedness. Verilog treats a bare vector as unsigned unless told otherwise, and if even one
operand in an expression is unsigned then the whole expression is evaluated as unsigned. Under that rule,
-3 * 4 quietly becomes 253 * 4. The registers here are declared `signed` and the multiply operands are wrapped
in `$signed()` as well, which is redundant but makes the intent impossible to misread.

Both of these are covered by tests that fail if the property is broken. Narrowing the product to 15 bits fails
test 5, and removing the signed handling fails the negative-operand tests.

## Running the tests

The test bench uses cocotb and Icarus Verilog.

```bash
brew install icarus-verilog
python3 -m venv ~/tt-env && source ~/tt-env/bin/activate
pip install -r test/requirements.txt
```

Then:

```bash
cd test
make
```

Twelve tests run. The first nine cover the required behaviour and the last three close gaps found by
re-reading the specification:

| # | Test                                | Checks                                                |
|---|-------------------------------------|-------------------------------------------------------|
| 1 | `test_01_positive_multiply`         | 3 * 4 = 12                                            |
| 2 | `test_02_accumulates`               | A second MAC adds on top: 12 + 30 = 42                |
| 3 | `test_03_negative_times_positive`   | -3 * 4 = -12, read as `0xFFFFFFF4`                    |
| 4 | `test_04_negative_times_negative`   | -3 * -4 = +12                                         |
| 5 | `test_05_most_negative_squared`     | -128 * -128 = +16384, the product-width case          |
| 6 | `test_06_most_positive_squared`     | 127 * 127 = 16129                                     |
| 7 | `test_07_byte_select_sweep`         | All four byte lanes, with no clock edge between reads |
| 8 | `test_08_reset_clears_all_state`    | Reset mid-sequence clears `acc`, `a_reg` and `b_reg`  |
| 9 | `test_09_reserved_opcodes_do_nothing` | Opcodes 101, 110 and 111 change nothing             |
| 10 | `test_10_clear_preserves_operands`  | CLEAR zeroes `acc` but leaves A and B loaded          |
| 11 | `test_11_accumulate_through_zero`   | A total can go negative and come back                 |
| 12 | `test_12_large_negative_byte_lanes` | Byte lanes read a total past -2^24 correctly          |

The test clock is set to 10 us per cycle, which looks absurdly slow for logic this small. That number is
chosen for the gate-level run rather than for the RTL. After hardening, these same tests are replayed against
the synthesised netlist, which is compiled with every gate given one nanosecond of delay. The multiply and add
path is roughly 25 to 35 gates deep, so a clock in the tens of nanoseconds would sample the result before it
had settled and fail a design that is actually fine. The real speed target is separate, and lives in
`CLOCK_PERIOD` in [src/config.json](src/config.json).

Every test drives and reads only the real chip pins. None of them reach inside the module to inspect a
register, because the same tests are re-run against the gate-level netlist after the design is hardened, and by
that point the internal register names no longer exist.

Test 8 has to prove that `a_reg` and `b_reg` were cleared even though neither has an output pin of its own. It
does this through the datapath: after a reset it loads a known non-zero value into one register, runs a MAC, and
checks the answer is still zero. That can only be true if the other register really did reset to zero.

## Accumulator width

The accumulator width is the parameter `ACC_W`, which defaults to 32:

```verilog
module tt_um_ThomasJ1214_mac8 #(
    parameter integer ACC_W = 32
) ( ... );
```

Nothing in the module hard-codes 32, so if the design ever needs to be smaller, changing that one number to 24
or 16 is the entire change. The byte-select mux reads through a 32-bit sign-extended view of the accumulator, so
the upper byte lanes keep returning numerically correct values at any of those widths rather than pointing at
bits that no longer exist. The design lints cleanly at 32, 24 and 16.

## What is Tiny Tapeout?

Tiny Tapeout is an educational project that aims to make it easier and cheaper than ever to get your digital and analog designs manufactured on a real chip.

To learn more and get started, visit https://tinytapeout.com.

## Resources

- [FAQ](https://tinytapeout.com/faq/)
- [Digital design lessons](https://tinytapeout.com/digital_design/)
- [Learn how semiconductors work](https://tinytapeout.com/siliwiz/)
- [Join the community](https://tinytapeout.com/discord)
- [Build your design locally](https://www.tinytapeout.com/guides/local-hardening/)
