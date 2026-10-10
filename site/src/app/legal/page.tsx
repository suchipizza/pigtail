import { GITHUB } from "@/lib";

export const metadata = { title: "Legal & privacy — Pigtail" };

const EMAIL = "rakotomalala.noemie@gmail.com";
const Mail = () => <a href={`mailto:${EMAIL}`}>{EMAIL}</a>;

export default function Legal() {
  return (
    <section className="page legal">
      <div className="eyebrow">Legal &amp; privacy</div>
      <h1>Legal &amp; privacy</h1>
      <p className="lead">
        Pigtail is free, open-source software (MIT license) that you run on your own computer. This page
        covers the example reports we publish, what data goes where, and the optional data sources you can
        add with your own keys. Last updated October 10, 2026.
      </p>

      <h2>Example reports</h2>
      <ul>
        <li><b>Independent and AI-assisted.</b> An AI model (Anthropic&apos;s Claude) reads public sources and drafts each report. Automated publication checks then minimize personal data, remove or flag sensitive claims, and shorten excerpts.</li>
        <li><b>May contain errors.</b> Every statement links to its source. Check the source before relying on a claim.</li>
        <li><b>Timing is not cause.</b> Events shown close together in time were observed together. That does not prove that one caused the other.</li>
        <li><b>Not affiliated.</b> Pigtail is not affiliated with or endorsed by the companies and projects in its reports, or by the platforms it cites, such as GitHub, Hacker News, Reddit and Product Hunt.</li>
        <li><b>Not advice.</b> Reports are not investment, legal or business advice.</li>
        <li><b>Third-party rights.</b> The MIT license covers Pigtail&apos;s code only. Third-party names, source material, excerpts and linked data remain subject to their own rights.</li>
      </ul>
      <p>
        <b>Corrections and removal.</b> If a report is wrong, or if you are named in one and want something
        corrected or removed, write to <Mail /> or open a <a href={`${GITHUB}/issues`} target="_blank" rel="noreferrer">GitHub issue</a>.
      </p>

      <h2>Privacy</h2>
      <p>
        <b>This website</b> sets no cookies and uses no analytics or tracking. It is hosted on GitHub Pages,
        and GitHub may log visits, including IP addresses, for security; see{" "}
        <a href="https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement" target="_blank" rel="noreferrer">GitHub&apos;s privacy statement</a>.
      </p>
      <p>
        <b>The Pigtail tool</b> runs on your computer. It has no account and no telemetry, and it sends
        nothing to Pigtail&apos;s maintainers. To build a report, it contacts these services directly,
        using your own keys where needed:
      </p>
      <ul>
        <li><b>Anthropic</b> (your API key): the target, search queries and the text of the public sources it reads, so that Claude can search the web and extract and summarize facts. This includes the titles of Reddit posts when you use the Reddit API.</li>
        <li><b>GitHub</b> (your token, optional): the repository&apos;s history, stars, releases and README.</li>
        <li><b>Hacker News search</b> (Algolia): searches for posts about the target.</li>
        <li><b>Public websites</b>: pages found by search, only where their robots.txt allows it.</li>
        <li><b>Reddit</b> (your own approved keys, optional): searches for posts about the target.</li>
        <li><b>Product Hunt</b> (your own token, optional): lookups of the product&apos;s launches.</li>
      </ul>
      <p>
        Reports and research data are saved only on your computer, in the output folder. Each service&apos;s
        own terms and privacy policy apply to what you send it.
      </p>
      <p>
        <b>People named in reports.</b> Published reports name founders and executives in their company
        role, use public sources only, and leave out private individuals, family and health details. You
        can ask for a correction or removal at any time (see above).
      </p>
      <p>
        <b>Email.</b> If you write to us, we use your message and address only to answer you.
      </p>

      <h2>Optional data sources</h2>
      <p>
        Pigtail can read Reddit and Product Hunt through their official APIs, but only with keys you create
        in your own accounts, and new Reddit access needs Reddit&apos;s approval. When you use such a source,
        you do so under your own agreement with that platform and must follow its terms, including{" "}
        <a href="https://redditinc.com/policies/data-api-terms" target="_blank" rel="noreferrer">Reddit&apos;s Data API Terms</a>{" "}
        and <a href="https://api.producthunt.com/v2/docs" target="_blank" rel="noreferrer">Product Hunt&apos;s API terms</a>,
        which both restrict commercial use. The data stays on your computer: Pigtail&apos;s maintainers do not
        receive, store or publish it, and reports on this website never include it. Pigtail never uses
        browser cookies, logged-in sessions or scraping to read Reddit or Product Hunt.
      </p>

      <h2>Software</h2>
      <p>
        Pigtail is provided under the <a href={`${GITHUB}/blob/main/LICENSE`} target="_blank" rel="noreferrer">MIT license</a>,
        &quot;as is&quot;, without warranty of any kind. Reports you generate yourself are yours; Pigtail&apos;s
        maintainers do not review or endorse them.
      </p>

      <h2>Contact</h2>
      <p>
        Pigtail is maintained by Noémie (<a href="https://github.com/suchipizza" target="_blank" rel="noreferrer">@suchipizza</a>). Email <Mail /> or open a{" "}
        <a href={`${GITHUB}/issues`} target="_blank" rel="noreferrer">GitHub issue</a>.
      </p>
    </section>
  );
}
