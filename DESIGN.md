---
name: HDD Idle Profiler
description: A quiet measurement worksheet for disk workload comparisons.
colors:
  canvas: "#f3f4ef"
  surface: "#fff"
  ink: "#202c2a"
  muted: "#53635f"
  line: "#d6ddd5"
  accent: "#17665b"
  amber: "#805613"
  red: "#a3342f"
  button-hover: "#e6ece5"
  button-hover-border: "#9cac9f"
  accent-hover: "#104e45"
  focus: "#2c8374"
  inset: "#eaf0e8"
  row-hover: "#f7f9f5"
  notice-ink: "#3e5048"
  error-surface: "#fff2ed"
  error-border: "#dcb4a5"
  empty-border: "#a2b19e"
  bar-track: "#e1e7dc"
  histogram: "#637857"
  input-border: "#97a595"
typography:
  headline:
    fontFamily: "Segoe UI, system-ui, sans-serif"
    fontSize: "clamp(28px, 3vw, 40px)"
    fontWeight: 650
    lineHeight: 1.18
    letterSpacing: "-0.025em"
  title:
    fontFamily: "Segoe UI, system-ui, sans-serif"
    fontSize: "22px"
    fontWeight: 650
    lineHeight: 1.18
    letterSpacing: "-0.015em"
  body:
    fontFamily: "Segoe UI, system-ui, sans-serif"
    fontSize: "15px"
    lineHeight: 1.5
  metric:
    fontFamily: "Segoe UI, system-ui, sans-serif"
    fontSize: "26px"
    fontWeight: 600
    letterSpacing: "-0.015em"
  table:
    fontFamily: "Segoe UI, system-ui, sans-serif"
    fontSize: "14px"
rounded:
  bar: "2px"
  input: "5px"
  control: "6px"
  surface: "8px"
spacing:
  small: "8px"
  compact: "12px"
  base: "16px"
  group: "24px"
  section: "36px"
  page: "40px"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.surface}"
    rounded: "{rounded.control}"
    padding: "10px 16px"
  button-primary-hover:
    backgroundColor: "{colors.accent-hover}"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "10px 16px"
  button-secondary-hover:
    backgroundColor: "{colors.button-hover}"
  input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.input}"
    padding: "10px 12px"
  table-container:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.surface}"
  notice:
    backgroundColor: "{colors.inset}"
    textColor: "{colors.notice-ink}"
    rounded: "{rounded.surface}"
    padding: "20px 24px"
---

# Design System: HDD Idle Profiler

## Overview

**Creative North Star: "The Quiet Measurement Worksheet"**

The interface is a light working sheet for comparing disk observations. Warm off-white surrounds white tables, dark ink carries the measurements, and restrained teal marks links and known-idle status. System typography and tabular numerals support repeated scanning alongside the Unraid console.

The implemented world is flat and practical. Labeled numerical bars complement comparison tables; explanatory text preserves the distinction between observed I/O and modeled power behavior. Decorative imagery and concept mockups have no role in the handoff's chosen interface.

**Key Characteristics:**

- Light working surfaces with fine structural borders.
- Modest system headings and tabular measurements.
- Explicit state labels and persistent collection status.
- Native controls, visible keyboard focus, and restrained state motion.

This document records the built interface in `app/static/app.css`, `app/templates/app.html`, and `app/static/app.js`. Frontmatter holds portable token primitives; `.impeccable/design.json` adds state, responsive and component-preview details.

## Colors

The palette combines warm paper and dark green ink with one principal teal accent. Amber and red communicate operational states.

### Primary

Measured teal (`accent`) identifies links, primary actions, healthy/idle status and timeout bars. Its deeper hover value distinguishes the primary action. Focus teal supplies the shared keyboard outline.

### Neutral

Worksheet paper (`canvas`) surrounds working white (`surface`) on tables, inputs, buttons and the header. Dark ink carries headings and readings; muted ink carries labels, notes and metadata. Fine rules (`line`) separate rows and sections. Pale inset surfaces support table headers and notices, with a darker notice text treatment. Row and control hover washes give restrained feedback. Bar tracks show unfilled extent; muted olive distinguishes the interval histogram from timeout charts.

Amber labels recently active state and cycling warnings. Red labels collection failures and destructive actions, reinforced by pale error surfaces and borders. Preliminary suggestions explicitly say “Preliminary” in text.

**The Labeled State Rule.** Color accompanies readable state text; it never supplies the only explanation.

## Typography

The entire interface uses Segoe UI with system-ui and sans-serif fallbacks. There is no separate display font or webfont request. Headings are modest, slightly tight and balanced; paragraph text is bounded at 72ch.

- Page titles use the headline role, fixed to 30px at the narrow breakpoint.
- Section headings use the title role, reducing to 20px on narrow screens; tertiary headings are 17px.
- Body descriptions and controls use the body scale. Supporting help is 13px and bounded at 58ch.
- Summary values use the metric role, reducing to 23px on narrow screens. Labels are 13px; notes are 12px, reducing to 11px.
- Tables use the table role, with 12px semibold column labels and 11px secondary cell metadata.
- Summaries, tables and bars use tabular numerals so measurements align while updating.

## Layout

The main sheet is centered and bounded at 1480px, with desktop padding of 40px 36px 56px. The white header places brand, navigation and collection status in one row, with an 80px minimum height. The footer shares the content bound and horizontal inset.

Summary measurements occupy a ruled grid with minimum item width 180px, 20px gaps and 22px vertical padding. Sections use open spacing instead of separate metric cards. Tables sit in full-width, horizontally scrollable wrappers. Chart pairs use equal columns with a 48px gap; settings use equal columns with a 64px gap. Disk checkboxes flow through columns at least 260px wide.

At 1000px and below, side padding becomes 24px, summaries use three columns and settings gaps narrow to 32px. At 650px and below, side padding becomes 20px, navigation wraps to a complete second header row, summaries use two columns, and charts and settings become single-column. Export actions and the save row stack. Tables preserve their structure and headers with horizontal scrolling and compact cell padding.

Spacing has a recurring 8px, 12px, 16px, 24px, 36px and 40px rhythm with optical exceptions. The built interface is not strictly restricted to four-pixel increments: controls and cells also use 10px/14px padding.

## Elevation & Depth

There are no shadows. White surfaces, pale insets, fine borders and section separation express hierarchy. Hover changes background color without lifting, scaling or translating elements. Button background transitions last 150ms with ease-out only when reduced motion is not requested.

## Shapes

Tables and notices use softly rounded surface corners; buttons have slightly tighter corners and inputs tighter still. Bars are almost square. One-pixel rules structure measurements; empty states have dashed borders. The status marker is a small circular dot beside text, without a pill container.

## Components

### Buttons

Primary save actions are solid teal with white text. Secondary actions and export links use white surfaces and fine borders. Both use semibold text and the control radius/padding from frontmatter. Secondary hover adds a pale wash and darker border; primary hover deepens teal. Disabled buttons are half-opacity with a default cursor. Destructive buttons retain the outline and use red text.

**The Visible Focus Rule.** Interactive elements use a 3px teal outline with 4px offset on keyboard focus. Preserve the skip link, table-wrapper keyboard access and focus on refreshed disk links and sort controls.

### Navigation and status

Navigation is text with a bottom rule on the current destination, darker text and increased weight. Disks remains current on disk detail pages. Navigation occupies a second header row on narrow screens. Collection status remains in the header with a 7px dot and a written label: connecting, collecting, needs attention or disconnected.

### Tables and summaries

White tables use pale headers, fine row dividers, tabular values and a subtle row hover wash. Desktop cells use 14px 16px padding; narrow cells use 12px. Numeric comparison cells align right. Disk links are bold; model/serial metadata wraps. Dashboard sort headers are native buttons with direction arrows and `aria-sort` state. Summary metrics are definition lists between horizontal rules.

### Labeled bars

Each row has a delay/bin label, a 22px-high track and a right-aligned number. Timeout comparisons use teal; the interval histogram uses muted olive and is bounded at 850px. The largest value within each chart fills its track. Tracks are hidden from assistive technology while text labels and values remain readable. Numerical timeout tables accompany the comparison charts.

### Inputs and disk selection

Labeled native inputs have white fill, a muted border and a maximum width of 500px. Help follows field groups. Native 18px checkboxes use the accent color; disk labels include model and eligibility information. Discovery refresh updates eligibility and text in place, preserving unsaved checked state and focused elements. Settings save explicitly with an adjacent status message.

### Notices, empty states and destructive actions

Informational notices use a pale inset and generous padding. Errors use the error surface, red text and an alert role. Connection failure retains the displayed observations and explains possible staleness. Empty installations show mount instructions and a settings link. Synthetic previews are labeled above the page content. History clearing has a separate ruled section, an export reminder and typed `CLEAR HISTORY` confirmation.

## Do's and Don'ts

### Do:

- **Do** keep measurements readable with tabular numerals, units, labels and comparison tables.
- **Do** preserve readable status text, visible focus and unsaved settings during refresh.
- **Do** use flat light surfaces and fine rules to organize dense operational information.
- **Do** label modeled results, unknown interval beginnings, preliminary suggestions and synthetic observations explicitly.

### Don't:

- **Don't** add decorative imagery, dramatic display typography or ornamental animation to this measurement interface.
- **Don't** imply that observed idle counters are actual disk standby readings.
- **Don't** hide table columns on mobile or make bars the sole source of numeric results.
- **Don't** add shadows or floating metric cards to the established ruled worksheet layout.
