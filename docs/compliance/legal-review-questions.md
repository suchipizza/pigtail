# Questions for the owner's lawyer (gate H2)

**Status:** draft for legal review. This is not legal advice. The compliance agent prepared it and is not a lawyer.
**Version:** 0.1 · 2026-09-25 · Task M3-T2
**For:** the owner's external lawyer. The lawyer should read, in this order:
1. [lia.md](lia.md)
2. [dpia.md](dpia.md)
3. [retention-policy.md](retention-policy.md)
4. [privacy-notice.md](privacy-notice.md)
5. [terms-memos.md](terms-memos.md) (per-source platform terms, TM-01 to TM-31)

**Access date:** every URL was accessed on 2026-09-25.

**Context the lawyer needs:**
- pigtail is open-source software (MIT, public repo) that anyone can self-host.
- The owner runs one deployment.
- Assume **commercial use** under every platform's terms.
- Hosting is in the EU or Switzerland.
- The LLM is Anthropic Claude, through one of two backends:
  - `api`: Commercial Terms plus DPA;
  - `subscription`: the operator's own Claude Pro/Max plan through the official Claude Code CLI, under the Consumer Terms.

**How to read each entry:**
- **Context:** the facts and the texts relied on.
- **Question:** what we need answered.
- **Default meanwhile:** the conservative rule pigtail applies until the answer arrives.
- **Blocks:** what stays off until then.

Priority:
- **A** = blocks release (M9) or a live processing activity.
- **B** = blocks one source or feature.
- **C** = confirms a default; nothing is blocked.

### Mapping to the questions in terms-memos.md

| terms-memos | here |
|---|---|
| Q1 | LQ-4 |
| Q2 | LQ-3 |
| Q3 | LQ-5 |
| Q4 | LQ-6 |
| Q5 | LQ-15 |
| Q6 | LQ-16 |
| Q7 | LQ-17 |
| Q8 | LQ-18 |
| Q9 | LQ-19 |
| Q10 | LQ-20 |
| Q11 | LQ-12 |
| Q12 | LQ-21 |

LQ-1, LQ-2, LQ-7 to LQ-11, LQ-13, LQ-14 and LQ-22 to LQ-26 are new, arising from the LIA and DPIA.

---

## Part 1: data protection (new questions from the LIA and DPIA)

### LQ-1 · Subscription-mode LLM processing: is Anthropic a processor or an independent controller? · Priority A
- **Context:**
  - In `subscription` mode pigtail sends redacted third-party public posts to Claude through the operator's Pro/Max account.
  - For EEA and Swiss consumers the counterparty is Anthropic Ireland, Limited ([Consumer Terms](https://www.anthropic.com/legal/consumer-terms), effective 8 October 2025). Anthropic's [Privacy Policy](https://www.anthropic.com/legal/privacy) (effective 10 September 2026) names Anthropic Ireland as the controller of consumer data.
  - The Privacy Policy "does not apply to content that we process on behalf of customers of our business offerings". No DPA exists for consumer accounts.
  - Consumer Materials are used for training if the setting is on. Even with it off, they are used for training when flagged for safety review or submitted as feedback.
  - Retention is 30 days (training off) or up to 5 years (training on). Flagged content is kept 2 years ([privacy center](https://privacy.claude.com/en/articles/10023548-how-long-do-you-store-my-data)).
  - ZDR is not available for Free, Pro or Max ([API retention](https://platform.claude.com/docs/en/manage-claude/api-and-data-retention)).
  - The Consumer Terms require the user to "ensure that you have all rights, licenses, and permissions that are necessary for us to process such Inputs".
- **Question:**
  1. In subscription mode, is the operator *disclosing* personal data to an independent controller (Anthropic Ireland) rather than using a processor?
  2. If so, what legal basis covers the disclosure (GDPR Art. 6(1)(f); FADP Art. 31)? Must the notice name Anthropic as a recipient and controller?
  3. Is the lack of an Art. 28 contract a breach?
  4. Is FADP Art. 16 (disclosure abroad) satisfied?
- **Default meanwhile:**
  - Subscription mode may be used only by the owner, for her own deployment, with the training setting off (H1).
  - Redaction runs before every call (ADR-006).
  - Once LLM coding of person-level mention text starts (A2/A3), `LLM_BACKEND=api` is **recommended** for it.
  - The privacy notice describes both backends.
- **Blocks:** subscription-mode coding of person-level text at scale (Tier 2 extraction, M5). It does not block pilot coding of a small, hand-picked sample.

### LQ-2 · Consumer Terms: "not … for any commercial or business purposes" · Priority A
- **Context:**
  - The EEA/Swiss Consumer Terms, §11 (liability section), say: "You agree that you will not use our Services for any commercial or business purposes and we and our Providers have no liability to you for any loss of profit…"
  - The Claude Code legal page says Free, Pro and Max use of Claude Code falls under the Consumer Terms. Running Claude Code inside products needs the Commercial Terms ([legal and compliance](https://code.claude.com/docs/en/legal-and-compliance)). It also says: "Advertised usage limits for Pro and Max plans assume ordinary, individual usage".
  - ADR-008 limits subscription mode to operator self-use.
- **Question:**
  1. Is that sentence a use restriction, or only a liability allocation?
  2. Does it bar the owner from running pigtail on her Pro/Max plan for (a) personal research, (b) a pigtail deployment that serves her business, or (c) producing open-source aggregate findings?
  3. Does the answer differ for a non-EEA/CH operator under the non-EEA Consumer Terms?
- **Default meanwhile:**
  - Subscription mode is only for the owner's own use.
  - Any deployment used commercially or serving others must use `api` (R15.6).
  - The operator guide says so.
- **Blocks:** the PRD §12 default `LLM_BACKEND=subscription` for commercial deployments. If the answer is restrictive, pigtail's default flips to `api` (a new ADR).

### LQ-7 · Public-figure exception in the codebook · Priority B
- **Context:**
  - Spread graphs and the planner are more useful when they can say "a post by [well-known developer or publication] triggered the burst".
  - FADP Art. 31(2)(f) recognises an overriding interest where the controller "collects personal data relating to a public figure that relate to that person's public activities" ([fedlex](https://www.fedlex.admin.ch/eli/cc/2022/491/en)). The GDPR has no equivalent provision.
  - Matched losers and maintainers of individual-owned repos are not public figures.
- **Question:**
  1. May the private UI and the internal mechanism cards name accounts that are public figures, such as prominent developers, journalists or companies, under Art. 6(1)(f)?
  2. What test should the codebook use for "public figure"? For example: verified organisation; ≥ N followers *and* self-described professional publisher; or press or newsletter outlets.
  3. May *published* aggregate findings name publications (e.g. "Hacker News front page", a named newsletter) and organisations?
- **Default meanwhile:**
  - Only organisations and publications are named, and only in the private UI.
  - No natural person is named in any output.
  - Public outputs name platforms and publication types only (CB-11, CB-14).
- **Blocks:** the public-figure rule in codebook v0 (M4); D2 public mode.

### LQ-8 · Spread graphs and reach ranking as profiling · Priority A
- **Context:**
  - R5.3 builds graphs whose nodes are pseudonymised accounts and whose edges are `published | redistributed | cited | replied`.
  - R5.5 ranks candidate triggers by timing and reach.
  - GDPR Art. 4(4) defines profiling as automated processing "to evaluate certain personal aspects … in particular to analyse or predict … personal preferences, interests, reliability, behaviour". Art. 21(1) gives a right to object to profiling based on Art. 6(1)(f).
  - FADP Art. 5(g) defines "high-risk profiling" as profiling "by matching data that allow an assessment to be made of essential aspects of the personality". Art. 6(7)(b) requires explicit consent for high-risk profiling by private persons.
  - X's Developer Agreement §XIV.B bars "tracking X users" (TM-15).
- **Question:**
  1. Are these graphs "profiling"?
  2. Could they amount to FADP high-risk profiling, which would need consent?
  3. Does restricting them to Tier 2 and Tier 3 cases (about 300 repos), pseudonymised nodes, reach bands and private-only display bring them within Art. 6(1)(f) and FADP Art. 31?
  4. Would identifying "who amplifies launches" for the growth engine (D4) turn into direct marketing, or into targeting of individuals?
- **Default meanwhile:** no account-level spread graphs are built. Community- and publication-level graphs only. The growth engine never suggests contacting a named private individual (CB-14).
- **Blocks:** R5.3 and R5.5 at account level (M5 and M6); the "people" part of D4.

### LQ-9 · Incidental special-category data in public posts · Priority C
- **Context:** posts can reveal political, religious or health information. GDPR Art. 9(2)(e) allows processing of data "manifestly made public by the data subject". FADP Art. 5(c) defines sensitive data.
- **Question:**
  1. Is storing raw snapshots that incidentally contain such data acceptable under Art. 9(2)(e) (for posters themselves), given that pigtail neither seeks nor codes it?
  2. What if the post reveals a *third person's* sensitive data?
- **Default meanwhile:** the codebook forbids coding such attributes (CB-11). Output schemas are closed. The 24-month limit applies.
- **Blocks:** nothing.

### LQ-10 · Art. 14 GDPR / Art. 19 FADP information duty: disproportionate effort · Priority A
- **Context:**
  - The data is not collected from the data subjects. GH Archive covers millions of accounts, and pigtail holds no contact data.
  - GDPR Art. 14(5)(b) exempts cases where information "proves impossible or would involve a disproportionate effort, in particular for … scientific or historical research purposes or statistical purposes", provided "appropriate measures" are taken, "including making the information publicly available".
  - FADP Art. 20(2)(b) exempts "disproportionate effort" when data is not collected from the data subject.
  - FADP Art. 60(1) provides fines of up to CHF 250,000, "on complaint", for private persons who wilfully breach their duties under Art. 19.
- **Question:**
  1. Is a published notice ([privacy-notice.md](privacy-notice.md)) enough for all sources?
  2. For the smaller Tier 2 and Tier 3 sets, where pigtail could reply to or mention posters, is individual notice required? We think it would be intrusive and possibly platform spam.
  3. Does a *commercial* operator qualify for the "research or statistical purposes" limb?
  4. Where must the notice be published for it to count?
- **Default meanwhile:** a public notice only, no individual contact (and no platform posting without H5). Person-level sources beyond GH Archive stay off until the notice is published (CB-12) and the other ADR-022 pre-conditions exist.
- **Blocks:** enabling Bluesky, HN and V2EX.

### LQ-11 · Is this "light" DPIA enough; national lists; prior consultation · Priority B
- **Context:**
  - [dpia.md](dpia.md) §1 concludes that a DPIA is required: WP248 rev.01 criteria 3, 5 and 6 are met.
  - It concludes there is no high residual risk once the planned controls exist, so no prior consultation under GDPR Art. 36 or FADP Art. 23.
  - We did not check the Art. 35(4) list of the operator's lead supervisory authority.
- **Question:**
  1. Does the DPIA meet Art. 35(7) and FADP Art. 22(3)?
  2. Which authority's list applies?
  3. Do you agree that no prior consultation is required?
- **Default meanwhile:** treat the DPIA as required and binding. Hold the processing whose controls are missing ([lia.md](lia.md) §7).
- **Blocks:** release (M9).

### LQ-13 · Territorial scope and representatives · Priority B
- **Context:**
  - GDPR Art. 3(2)(b) applies to non-EU controllers "monitoring … behaviour" of people in the Union. Recital 24 refers to whether persons "are tracked on the internet".
  - Art. 27 requires an EU representative unless the processing is occasional and low-risk.
  - FADP Art. 3(1) applies to "circumstances that have an effect in Switzerland". Art. 14 requires a Swiss representative from foreign private controllers when processing is regular, large-scale, high-risk and linked to monitoring people in Switzerland.
- **Question:**
  1. If the owner is established in Switzerland, does continuous capture of EU residents' public activity count as "monitoring of their behaviour" under Art. 3(2)(b), so that an EU representative (Art. 27) is needed?
  2. For a self-hoster outside both the EU and Switzerland, what applies?
- **Default meanwhile:** the notice has a representative placeholder. No public release.
- **Blocks:** release (M9), if a representative is needed.

### LQ-14 · Controller role of self-hosters and of the pigtail project · Priority B
- **Context:**
  - Each operator self-hosts and decides which sources to enable (PRD §3). The pigtail maintainers publish code, defaults and connector clearances, but never see the operators' data.
  - See EDPB Guidelines 07/2020 on the concepts of controller and processor (final, adopted 7 July 2021, [PDF](https://edpb.europa.eu/system/files/documents/2023-10/EDPB_guidelines_202007_controllerprocessor_final_en.pdf)).
- **Question:**
  1. Is each self-hoster the sole controller of its deployment?
  2. Could the project maintainers be joint controllers (Art. 26) because they set the "means" (connectors, defaults, retention)?
  3. What should the README and operator guide say to avoid that, and to pass the duties on clearly (CB-21)?
- **Default meanwhile:** the docs state that each operator is the controller and must adopt its own LIA, DPIA and notice. The project maintainers collect no operator data (no telemetry in pigtail).
- **Blocks:** release (M9) and any third-party self-hosting guidance.

### LQ-22 · Is pseudonymised or redacted text personal data *for the LLM provider*? · Priority C
- **Context:**
  - GDPR Recital 26: pseudonymised data "should be considered to be information on an identifiable natural person".
  - EDPB Guidelines 01/2025 on Pseudonymisation (adopted 16 January 2025, public consultation; [PDF](https://www.edpb.europa.eu/system/files/2025-01/edpb_guidelines_202501_pseudonymisation_en.pdf)).
  - Secondary sources report that the CJEU in *EDPS v SRB* (C-413/23 P, judgment of 4 September 2025) held that pseudonymised data need not be personal data for a recipient without reasonable means of re-identification. **We did not verify this on curia.europa.eu**, because the official page redirected and was not read.
  - The text sent to Anthropic is public text, so it can be found again by searching the web.
- **Question:** does any of this change the analysis in LQ-1 or the transfer analysis? We assume it does not, because public text is searchable.
- **Default meanwhile:** treat everything sent to the LLM as personal data.
- **Blocks:** nothing.

### LQ-23 · Swiss FADP specifics · Priority B
- **Context and question:**
  - (a) Art. 30(3): no breach where the data subject "makes the personal data generally accessible and has not explicitly prohibited any processing". Does a platform-level signal (Bluesky user intents, a "no AI" note in a profile, robots or AI-content signals) count as an "explicit" prohibition that pigtail must honour per person?
  - (b) Does case forensics fit Art. 31(2)(e) ("purposes not related to specific persons")? If only partly, is the general overriding-interest balance enough?
  - (c) Personal criminal exposure: Art. 60 and Art. 61 provide fines up to CHF 250,000 on complaint against **private persons** who act wilfully (for example Art. 61(a), disclosure abroad in breach of Art. 16). Given that, what should an individual operator (the owner) do in addition, e.g. subscription-mode disclosure to the US?
  - (d) Is a Record of processing activities (Art. 12) required, or does the Art. 12(5) small-company exception apply?
- **Default meanwhile:** honour explicit refusals when known (CB-13); api mode for any disclosure abroad at scale (LQ-1); draft a record of processing activities (CB-15).
- **Blocks:** Bluesky connector (a); release (M9).

### LQ-24 · Naming projects and maintainers in outcome classes · Priority B
- **Context:**
  - Outcome classes (`winner`, `attention_only`, `short_lived`, `plateau`, `slow_riser`, PRD §8.2) are about projects. For repos owned by individuals, the `owner/repo` name identifies the maintainer.
  - Publishing "X/project was a matched loser" could harm reputation (civil-law personality protection, FADP Art. 32(2) actions).
- **Question:** may aggregate findings or case studies name projects in the loser classes, and name projects owned by individuals? Under what conditions (consent, neutral wording, organisations only)?
- **Default meanwhile:** public outputs name neither matched losers nor repos owned by personal accounts without the owner's consent. Organisation-owned winners may be named (CB-20).
- **Blocks:** D2 public mode; publication of case studies (M9).

### LQ-25 · Raw GH Archive dumps: proportionality of keeping them for replay · Priority B
- **Context:**
  - Whole hourly dumps are stored raw. They contain logins, avatar URLs and issue and comment bodies, and older dumps may contain commit author e-mail addresses. The analysis uses six fields.
  - The proposed fix (CB-04) is to keep the hash, drop the bytes after ≤ 30 days, and have replay re-download and verify.
- **Question:** is the 30-day raw window plus hash-based replay proportionate? Or must the raw dumps be dropped immediately after parsing?
- **Default meanwhile:** CB-04 must be implemented before the capture runs on the production host. Until then the capture runs only locally for testing.
- **Blocks:** production capture on the host (M1 acceptance).

### LQ-26 · Keeping "hash + coded facts" after an upstream deletion · Priority B
- **Context:**
  - R1.5 keeps the content hash and coded facts after the raw copy is dropped.
  - Bluesky's guidelines require "a method for deleting content a user has requested to be deleted" (TM-06).
  - Reddit's terms say retention of deleted content "even if … de-identified or anonymized" violates them (TM-05; LQ-3 covers Reddit specifically).
- **Question:**
  1. Once pseudonyms and quoted spans are removed, do non-identifying coded facts (e.g. "a launch post appeared on platform P at time T with reach band B") still count as personal data, or as "content" under Bluesky's guideline?
  2. Can they be kept for research after an erasure request (GDPR Art. 17(3)(d) research exemption)?
- **Default meanwhile:** on upstream deletion or erasure, delete everything carrying a pseudonym, text or quote. Keep only hash, time, platform and non-identifying coded facts.
- **Blocks:** nothing, as long as the default is applied.

### LQ-12 (was Q11) · Adequacy of 24-month retention plus pseudonymisation at ingest · Priority A
- **Context:** carried over from terms-memos Q11:
  > "For people whose public posts appear in private snapshots, is our 24-month retention for person-level data, together with pseudonymisation at ingest, adequate under GDPR and the Swiss FADP? Is it compatible with each CLEARED-WITH-CONDITIONS source?"

  The LIA and DPIA are now drafted. Note: "pseudonymisation at ingest" applies to *parsed records*. Raw snapshots keep handles (DPIA R1 and R4).
- **Question:**
  1. As in Q11.
  2. In addition: is the policy in [retention-policy.md](retention-policy.md) adequate, including the 35-day backups, 12-month logs, 24-month LLM cache and tombstones?
- **Default meanwhile:** the policy as drafted. No new person-level source until every ADR-022 pre-condition exists (CB-01, CB-02, CB-03, CB-06, CB-08, CB-12, CB-13).
- **Blocks:** release (M9); person-level sources beyond GH Archive.

---

## Part 2: platform terms (carried over from terms-memos.md Q1–Q10 and Q12, unchanged in substance)

### LQ-4 (was Q1) · GitHub / GH Archive: commercial processing of personal event data · Priority A
- **Context:**
  - AUP §7 allows research use of "public, non-personal information … only if any publications resulting from that research are open access", and use must "comply with the GitHub Privacy Statement".
  - GH Archive states no licence for its data. The maintainer's answer on issue #137 is "The API is free to use, but you need to follow our Terms of Service" (TM-01).
- **Question** (as in Q1): Can a commercial operator lawfully store and analyse pseudonymised GitHub event data about individuals (actor logins, contribution timelines)? Consider GitHub AUP §7 and GDPR/FADP legitimate interest. Does GH Archive's lack of a licence matter?
- **Default meanwhile:**
  - TM-01 conditions (pseudonymise, never sell or export, aggregate-only open-access outputs);
  - CB-04 minimisation;
  - public findings released as open access.
- **Blocks:** release (M9). Does not block collection (WORK_ORDER §5).

### LQ-3 (was Q2) · Reddit · Priority B
- **Question** (as in Q2): Reddit's Wiki says that keeping deleted content, "even if … de-identified or anonymized", violates its terms. If a Reddit commercial agreement is ever signed:
  1. Could pigtail keep a content hash plus coded, non-verbatim facts about deleted content (R1.5)?
  2. Does sending text to an LLM provider under a DPA count as "sharing with a third party" under Developer Terms §7.2?
- **Default meanwhile:** Reddit is a documented GAP (TM-05, ADR-010).
- **Blocks:** Reddit connector.

### LQ-5 (was Q3) · GitHub ToS D.9 "Access Reciprocity" · Priority C
- **Question** (as in Q3): Is LLM *inference* on GitHub content outside this clause, which covers developing or training commercially available AI models?
- **Default meanwhile:** inference only, never training.
- **Blocks:** nothing.

### LQ-6 (was Q4) · Hacker News · Priority B
- **Question** (as in Q4):
  1. Do YC's public API invitations (the HackerNews/API README, and the Algolia API run with HN) count as "expressly authorized" under the YC Terms of Use? If so, commercial operators may use the API despite the ToU's bans on commercial reproduction and scraping.
  2. Is storing snapshots privately "reproduc[ing] … for commercial purposes"?
- **Default meanwhile:** API only, private snapshots, no republication of comment text (TM-03, TM-04). Not enabled until every ADR-022 pre-condition for person-level sources exists: CB-01, CB-02, CB-03, CB-06, CB-08, CB-12 and CB-13 (ADR-022 in `ops/DECISIONS.md` is the single authoritative list).
- **Blocks:** HN connectors for commercial operators.

### LQ-15 (was Q5) · Internet Archive · Priority B
- **Question** (as in Q5):
  1. Can a commercial operator use CDX lookups and Save Page Now for project-level pages, given the ToU's "scholarship and research purposes only" wording?
  2. Does requesting a public capture differ legally from using the collections?
- **Default meanwhile:** off by default for commercial operators (TM-13, ADR-010).
- **Blocks:** Wayback connector; evidence for pricing and careers pages (TM-29, TM-31).

### LQ-16 (was Q6) · Open-data licences · Priority C
- **Question** (as in Q6):
  1. The PyPI BigQuery tables are CC BY 4.0. Is that grant enough for commercial storage and analysis?
  2. Does deps.dev's CC-BY 4.0 grant override the Google APIs Terms clause against building databases?
  3. Are the crates.io dumps under any licence?
- **Default meanwhile:** TM-07, TM-09 and TM-12 conditions (attribution, byte caps, point lookups).
- **Blocks:** nothing.

### LQ-17 (was Q7) · Sources with no stated terms · Priority C
- **Question** (as in Q7):
  1. For sources with no terms or data licence (Homebrew analytics, V2EX, and Lobste.rs apart from robots.txt), is low-rate API access to aggregate or public data acceptable?
  2. What attribution should we give?
- **Default meanwhile:** TM-10 and TM-19 conditions. V2EX is off.
- **Blocks:** nothing (V2EX is covered by LQ-18).

### LQ-18 (was Q8) · China PIPL · Priority B
- **Question** (as in Q8):
  1. Does pseudonymised processing in the EU or Switzerland of public posts by users in China (from V2EX, and any future Chinese source) trigger PIPL Art. 3(2)? If it does, it would also trigger the Art. 53 duty to appoint a representative in China.
  2. Is aggregate, project-mention-only use a safe harbour?
- **Default meanwhile:** V2EX is disabled by default (TM-19).
- **Blocks:** V2EX and any Chinese source; the Chinese-ecosystem module (R6.2) evidence.

### LQ-19 (was Q9) · X · Priority C (X is a gap)
- **Question** (as in Q9): If an operator had an X Enterprise agreement, would case forensics that link accounts in a spread graph (R5.3) breach X's rules on "tracking X users" and surveillance (Developer Agreement §XIV.B)? See also LQ-8.
- **Default meanwhile:** X is a GAP (TM-15).
- **Blocks:** X connector.

### LQ-20 (was Q10) · dev.to · Priority C (gap)
- **Question** (as in Q10): Does publishing the Forem API imply a licence that overrides the site terms' "non-commercial transitory viewing only" wording for API data?
- **Default meanwhile:** GAP (TM-18).
- **Blocks:** dev.to connector.

### LQ-21 (was Q12) · Discord invite counts · Priority C
- **Question** (as in Q12):
  1. Under the Discord Developer Policy (items 18 and 20) and the Discord ToS, may a commercial operator poll the documented `GET /invites/{code}?with_counts=true` endpoint once a day, for invite codes that projects publish themselves, and store only the aggregate member counts for internal analysis?
  2. Does that count as "mining or scraping" or "commercializ[ing]" API Data?
- **Default meanwhile:** disabled by default for commercial operators (TM-27). The community-size metric is `unknown` or `self_reported`.
- **Blocks:** Discord connector.

---

## Summary for the owner

| Priority | Questions | Blocks if unanswered |
|---|---|---|
| **A** | LQ-1, LQ-2, LQ-4, LQ-8, LQ-10, LQ-12 | Release (M9); subscription-mode coding at scale; account-level spread graphs; person-level sources beyond GH Archive (until the notice and controls exist) |
| **B** | LQ-3, LQ-6, LQ-7, LQ-11, LQ-13, LQ-14, LQ-15, LQ-18, LQ-23, LQ-24, LQ-25, LQ-26 | The named source or feature |
| **C** | LQ-5, LQ-9, LQ-16, LQ-17, LQ-19, LQ-20, LQ-21, LQ-22 | Nothing (the defaults stand) |

## Changelog
- 2026-09-25: v0.1 created (M3-T2).
- 2026-09-25 — fixes after verifier M3 round 1: LQ-6 (and the LQ-10 and LQ-12 defaults) now refer to ADR-022 and its full list of pre-conditions for person-level sources.
