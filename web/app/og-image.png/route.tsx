/* eslint-disable @next/next/no-img-element */
import { ImageResponse } from "next/og"
import type { NextRequest } from "next/server"

export const runtime = "edge"

const TITLE = "Guardrails for Claude Code"
const SUB = "Every tool call checked against your rules. Block, queue, and seal it into a tamper-evident ledger."
const BRAND_ORANGE = "#DD4B2D"

export async function GET(request: NextRequest): Promise<ImageResponse> {
  const origin = new URL(request.url).origin
  const logoUrl = `${origin}/openmagi-app-icon.png`

  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          alignItems: "stretch",
          justifyContent: "flex-start",
          background: "#fafaf8",
          backgroundImage:
            "linear-gradient(rgba(15,23,42,0.055) 1px, transparent 1px), linear-gradient(90deg, rgba(15,23,42,0.055) 1px, transparent 1px)",
          backgroundSize: "44px 44px",
          color: "#0b0f19",
          fontFamily: "system-ui, -apple-system, sans-serif",
          overflow: "hidden",
          position: "relative",
          padding: "68px 72px",
        }}
      >
        <div
          style={{
            position: "absolute",
            top: 0,
            left: 0,
            right: 0,
            height: "8px",
            background: BRAND_ORANGE,
          }}
        />
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            justifyContent: "center",
            width: "1040px",
            height: "100%",
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: "20px",
              marginBottom: "44px",
            }}
          >
            <img
              src={logoUrl}
              alt="Open Magi"
              width={104}
              height={104}
              style={{ objectFit: "contain" }}
            />
            <div style={{ display: "flex", flexDirection: "column" }}>
              <span style={{ fontSize: "40px", fontWeight: 800, lineHeight: 1 }}>
                Open Magi
              </span>
              <span
                style={{
                  color: BRAND_ORANGE,
                  fontSize: "20px",
                  fontWeight: 800,
                  letterSpacing: "0.14em",
                  marginTop: "9px",
                  textTransform: "uppercase",
                }}
              >
                Control Plane
              </span>
            </div>
          </div>

          <h1
            style={{
              fontSize: "70px",
              fontWeight: 900,
              letterSpacing: 0,
              lineHeight: 1.02,
              margin: "0 0 28px",
            }}
          >
            {TITLE}
          </h1>

          <p
            style={{
              color: "#475569",
              fontSize: "26px",
              fontWeight: 500,
              lineHeight: 1.34,
              margin: "0",
              maxWidth: "900px",
            }}
          >
            {SUB}
          </p>
        </div>

        <div
          style={{
            position: "absolute",
            left: "72px",
            bottom: "42px",
            display: "flex",
            color: "#64748b",
            fontSize: "20px",
            fontWeight: 700,
          }}
        >
          cp.openmagi.ai
        </div>
      </div>
    ),
    {
      width: 1200,
      height: 630,
    },
  )
}
