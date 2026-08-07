/*
 * 8x8 signed multiply-accumulate (MAC) unit
 *
 * Copyright (c) 2026 Thomas Jenkins
 * SPDX-License-Identifier: Apache-2.0
 *
 * One processing element of a systolic array. On each clock it can load an
 * operand, or fold a new product into a running total:
 *
 *     acc <= acc + (A * B)
 *
 * A and B are signed 8-bit two's-complement values. The chip has only eight
 * output pins and the accumulator is 32 bits wide, so the result is read back
 * one byte at a time through a byte-select mux.
 */

`default_nettype none

module tt_um_ThomasJ1214_mac8 #(
    // Width of the accumulator. This is a parameter so it can be dropped to 24
    // or 16 if the design turns out not to fit its tile. Nothing below
    // hard-codes 32, so changing this one number is the whole change.
    parameter integer ACC_W = 32
) (
    input  wire [7:0] ui_in,    // Dedicated inputs:  operand data bus
    output wire [7:0] uo_out,   // Dedicated outputs: selected accumulator byte
    input  wire [7:0] uio_in,   // IOs: Input path  = opcode and byte select
    output wire [7:0] uio_out,  // IOs: Output path = unused, tied low
    output wire [7:0] uio_oe,   // IOs: Enable path = all inputs, so tied low
    input  wire       ena,      // always 1 when the design is powered
    input  wire       clk,      // clock
    input  wire       rst_n     // reset, active LOW
);

  //--------------------------------------------------------------------------
  // Operand and product widths
  //--------------------------------------------------------------------------
  localparam integer A_W = 8;
  localparam integer B_W = 8;

  // A signed A_W x B_W product needs exactly A_W + B_W bits: no more, and
  // critically no fewer. The boundary case is the most negative input squared:
  //
  //     -128 * -128 = +16384 = 2**14
  //
  // Every other 8x8 product fits in 15 bits, so it is tempting to save a bit.
  // Do that and +16384 lands on the sign bit of a 15-bit word and reads back
  // as -16384, silently, with no warning from any tool. Hence the deliberate 16.
  localparam integer PROD_W = A_W + B_W;  // = 16

  //--------------------------------------------------------------------------
  // Opcodes, driven on uio_in[2:0]
  //--------------------------------------------------------------------------
  localparam [2:0] OP_NOP    = 3'b000,
                   OP_LOAD_A = 3'b001,
                   OP_LOAD_B = 3'b010,
                   OP_MAC    = 3'b011,
                   OP_CLEAR  = 3'b100;
  // 101, 110 and 111 are unassigned and fall through to the default (no-op).

  wire [2:0] opcode   = uio_in[2:0];
  wire [1:0] byte_sel = uio_in[4:3];

  //--------------------------------------------------------------------------
  // State
  //--------------------------------------------------------------------------
  reg signed [  A_W-1:0] a_reg;
  reg signed [  B_W-1:0] b_reg;
  reg signed [ACC_W-1:0] acc;

  // The $signed() casts are the whole ballgame. Verilog treats a bare vector as
  // UNSIGNED by default, and if even one operand in an expression is unsigned
  // then the entire expression is evaluated unsigned. Drop these casts and
  // -3 * 4 quietly becomes 253 * 4 instead of -12. The registers above are
  // already declared signed, so this is belt and braces, but it is the most
  // common way to get a silently wrong answer in signed Verilog and so it is
  // spelled out.
  wire signed [PROD_W-1:0] product = $signed(a_reg) * $signed(b_reg);

  // Widen the product to the accumulator width before adding, so the sign
  // extension is a visible step rather than something implied by the language.
  // Assigning a signed value to a wider signed wire sign-extends it, which is
  // what is wanted here.
  //
  // The linter flags this deliberate 16-bit to 32-bit widening, so it is
  // silenced for the one line. The usual alternative, writing the extension out
  // as {{(ACC_W-PROD_W){product[PROD_W-1]}}, product}, is avoided because it
  // becomes a zero-width replication, and therefore illegal, if ACC_W is ever
  // reduced to 16.
  /* verilator lint_off WIDTHEXPAND */
  wire signed [ACC_W-1:0] product_ext = product;
  /* verilator lint_on WIDTHEXPAND */

  always @(posedge clk) begin
    if (!rst_n) begin
      // Synchronous reset: smaller in area than an asynchronous one, and there
      // is no requirement here to clear state while the clock is stopped.
      a_reg <= {A_W{1'b0}};
      b_reg <= {B_W{1'b0}};
      acc   <= {ACC_W{1'b0}};
    end else begin
      case (opcode)
        OP_LOAD_A: a_reg <= $signed(ui_in);
        OP_LOAD_B: b_reg <= $signed(ui_in);
        // Overflow wraps around. No saturation was asked for.
        OP_MAC:    acc   <= acc + product_ext;
        OP_CLEAR:  acc   <= {ACC_W{1'b0}};
        // An empty statement. Inside a clocked block a register that is not
        // assigned simply keeps its value, which is what a no-op means.
        OP_NOP:    ;
        // 101, 110 and 111 are unassigned and behave the same as NOP. A case
        // without a default can infer a latch, so this arm is never omitted.
        default:   ;
      endcase
    end
  end

  //--------------------------------------------------------------------------
  // Output mux
  //--------------------------------------------------------------------------
  // A fixed 32-bit view of the accumulator. acc is signed, so if ACC_W is ever
  // reduced this sign-extends rather than reading garbage, and the upper byte
  // lanes keep returning the numerically correct answer instead of pointing at
  // bits that no longer exist. At the default ACC_W of 32 it is a plain copy.
  // Same deliberate widening as product_ext, silenced the same way.
  /* verilator lint_off WIDTHEXPAND */
  wire signed [31:0] acc_view = acc;
  /* verilator lint_on WIDTHEXPAND */

  reg [7:0] out_byte;

  // Combinational on purpose: changing the byte select has to show a new byte
  // straight away, with no clock edge needed to shift the value out.
  always @(*) begin
    case (byte_sel)
      2'b00:   out_byte = acc_view[7:0];
      2'b01:   out_byte = acc_view[15:8];
      2'b10:   out_byte = acc_view[23:16];
      2'b11:   out_byte = acc_view[31:24];
      // All four values of a 2-bit select are already covered, so this can
      // never be reached in hardware. It is here because a case without a
      // default infers a latch if the tool cannot prove completeness.
      default: out_byte = acc_view[7:0];
    endcase
  end

  assign uo_out  = out_byte;

  // Every bidirectional pin is used as an input, so nothing is driven outwards
  // and the output enables stay low.
  assign uio_out = 8'h00;
  assign uio_oe  = 8'h00;

  // Tell the linter these really are meant to go nowhere, rather than being an
  // oversight. ena is always 1 on a powered design, and uio_in[7:5] is spare.
  wire _unused = &{ena, uio_in[7:5], 1'b0};

endmodule
