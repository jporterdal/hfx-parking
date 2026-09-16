## REMOVED Requirements

This capability is retired in full. It specified a board that is one generated file, and this change replaces that delivery model with an application served at a URL, specified in `hosted-triage-app`.

Retiring it rather than modifying it is the honest structure. Five of its nine requirements rest on the file being the product — that it is self-contained, built alongside the briefs, carries a build time, and shares decisions only where the hosting environment happens to offer storage. Those premises do not survive. The remaining four describe behaviour that is required unchanged and is restated in `hosted-triage-app`, where it belongs alongside the routing, filtering and freshness the served application adds.

No behaviour is dropped. Every requirement below names where it now lives.

### Requirement: Ship as one self-contained file

**Reason**: A URL replaces the file. The reasoning behind this requirement — "a work list that needs infrastructure to open is a work list nobody opens" — is preserved, not abandoned: a served application keeps the infrastructure entirely on the server's side, and the viewer still needs nothing but a browser.

**Migration**: `hosted-triage-app`, "Serve the lists over HTTP", which keeps the constraint that no install, account, credential or build step may be required of a viewer. The file's genuinely lost properties — mailable, archivable, openable with no network — are preserved by "Export a list as a file".

### Requirement: Cover every tracked violation type

**Reason**: Satisfied by routing rather than by embedding every type's data in one file with a viewer-facing switcher. Under a served application a view carries one type's payload instead of all of them.

**Migration**: `hosted-triage-app`, "Route by canonical violation type", which keeps the prohibition on mixing doorways or blocks across types.

### Requirement: Present both the doorway list and the block list

**Reason**: Required unchanged; it belongs with the served views.

**Migration**: `hosted-triage-app`, "Present both lists with their per-row detail".

### Requirement: Build the board from the same run that writes the briefs

**Reason**: There is no build. The guarantee this requirement bought — that the page a person opens cannot disagree with the files beside it — is now provided by the data being fixed between syncs rather than by the outputs being written together. Its second scenario, reporting absent build inputs, describes a step that no longer exists.

**Migration**: `hosted-triage-app`, "Export a list as a file", which requires a view and an export to agree on every count and to name the same mirror state.

### Requirement: Locate each doorway on a map of Halifax

**Reason**: Required unchanged, with one addition: the geometry is served as a cacheable asset rather than embedded into every view's payload.

**Migration**: `hosted-triage-app`, "Locate each doorway on a map of Halifax" and "Serve map geometry as a cacheable asset".

### Requirement: Record a triage decision per doorway and per block

**Reason**: Required unchanged, and strengthened: decisions gain server-side persistence, role attribution and a last-changed time.

**Migration**: `hosted-triage-app`, "Persist triage decisions to shared storage" and "Show when a decision was last changed and by which role".

### Requirement: Share decisions where possible and disclose when they are private

**Reason**: The conditional is gone. Shared triage had exactly one implementation — a Claude Artifact capability — which resolves to nothing on any other host, silently degrading every viewer to a private copy. The hosted application persists decisions server-side unconditionally, so there is no private fallback left to disclose.

The disclosure principle behind this requirement is the part worth keeping, and it is kept: a viewer must never be left believing their triage reached colleagues when it did not. It is redirected at what a viewer now cannot otherwise see — how fresh the data is, and that a selected role identifies rather than authenticates.

**Migration**: `hosted-triage-app`, "Persist triage decisions to shared storage" for the unconditional guarantee, "Display data freshness prominently, and name whose limit it is" and "Identify a viewer by role, not by account" for the redirected disclosure.

### Requirement: Show when the board was built

**Reason**: Build time is the least informative clock available once the application renders a view on every request regardless of whether any data arrived. What tells a reader whether to trust the list is when the source last published, how recent that data is, when the mirror last synced, and whether a sync is overdue.

**Migration**: `hosted-triage-app`, "Display data freshness prominently, and name whose limit it is" and "Treat the next update as a state, not a rendered date". The template's undated "regenerated from the live service" assertion is removed rather than reworded, because it is the kind of claim that stays true-looking after it stops being true.

### Requirement: Remain readable in light and dark

**Reason**: Required unchanged; it belongs with the served views.

**Migration**: `hosted-triage-app`, "Remain legible in light and dark".
