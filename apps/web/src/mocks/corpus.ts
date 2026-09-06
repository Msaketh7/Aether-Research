import type {
  ClaimStatus,
  ClaimType,
  EvidenceStance,
  ReportSectionKind,
  SourceType,
  TaskPriority,
} from '@aether/shared-types';

/**
 * The fixture corpus behind the mock API.
 *
 * SYNTHETIC DATA. Company names and publishers are real so the interface looks
 * like the product it will become, but every figure, quote and finding below is
 * invented for demonstration. The app renders a persistent "demo data" banner
 * whenever NEXT_PUBLIC_API_MODE=mock so this can never be mistaken for research
 * output. It is deleted the moment the real backend lands (Phase 2).
 */

export interface SubtaskSeed {
  external_id: string;
  question: string;
  priority: TaskPriority;
  rationale: string;
  /** Which loop iteration created it. 2 = added by the critic. */
  iteration: number;
}

export const SUBTASKS: readonly SubtaskSeed[] = [
  {
    external_id: 'market',
    question: 'How large is the AI inference infrastructure market and how fast is it growing?',
    priority: 'high',
    rationale: 'Sizing the opportunity bounds every other conclusion.',
    iteration: 1,
  },
  {
    external_id: 'competitors',
    question: 'Who are the major providers and how do their offerings differ?',
    priority: 'high',
    rationale: 'The question is explicitly comparative.',
    iteration: 1,
  },
  {
    external_id: 'technology',
    question: 'What serving stacks and accelerators do the leading providers rely on?',
    priority: 'high',
    rationale: 'Technical differentiation drives durable margin.',
    iteration: 1,
  },
  {
    external_id: 'pricing',
    question: 'How is inference priced across providers, and per what unit?',
    priority: 'high',
    rationale: 'Pricing is the most directly comparable axis.',
    iteration: 1,
  },
  {
    external_id: 'financial',
    question: 'What funding has each provider raised, and what is disclosed about revenue?',
    priority: 'medium',
    rationale: 'Capital position determines who can sustain a price war.',
    iteration: 1,
  },
  {
    external_id: 'recent-news',
    question: 'What material announcements have these companies made in the last 12 months?',
    priority: 'medium',
    rationale: 'The date filter requires recency-scoped discovery.',
    iteration: 1,
  },
  {
    external_id: 'risk',
    question: 'What are the principal risks to inference providers?',
    priority: 'medium',
    rationale: 'A comparison without downside analysis is incomplete.',
    iteration: 1,
  },
  {
    external_id: 'gross-margin',
    question: 'What gross margins are achievable at current GPU rental costs?',
    priority: 'high',
    rationale: 'Critic: pricing coverage lacked a unit-economics dimension.',
    iteration: 2,
  },
];

export interface SourceSeed {
  title: string;
  publisher: string;
  url: string;
  source_type: SourceType;
  /** Days before the run started. */
  published_days_ago: number;
  excerpt: string;
  tier: 'official' | 'reputable' | 'community' | 'unknown';
  is_primary: boolean;
  task: string;
  author?: string;
}

export const SOURCES: readonly SourceSeed[] = [
  {
    title: 'Together AI announces expanded inference cluster capacity',
    publisher: 'Together AI',
    url: 'https://www.together.ai/blog/inference-capacity-expansion',
    source_type: 'web',
    published_days_ago: 34,
    excerpt:
      'The company describes an expansion of its dedicated inference fleet and reports throughput improvements on its serving stack.',
    tier: 'official',
    is_primary: true,
    task: 'recent-news',
  },
  {
    title: 'Fireworks AI pricing page',
    publisher: 'Fireworks AI',
    url: 'https://fireworks.ai/pricing',
    source_type: 'web',
    published_days_ago: 6,
    excerpt:
      'Per-million-token pricing for serverless inference, tiered by model size, with separate rates for dedicated deployments.',
    tier: 'official',
    is_primary: true,
    task: 'pricing',
  },
  {
    title: 'Baseten raises Series C to scale model deployment platform',
    publisher: 'TechCrunch',
    url: 'https://techcrunch.com/baseten-series-c',
    source_type: 'web',
    published_days_ago: 88,
    excerpt:
      'Coverage of the funding round, investor syndicate, and the company positioning around production model deployment.',
    tier: 'reputable',
    is_primary: false,
    task: 'financial',
  },
  {
    title: 'CoreWeave Form 10-Q, quarterly report',
    publisher: 'SEC EDGAR',
    url: 'https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company=coreweave',
    source_type: 'sec',
    published_days_ago: 45,
    excerpt:
      'Quarterly filing including revenue, cost of revenue, capital expenditure on GPU infrastructure and customer concentration disclosures.',
    tier: 'official',
    is_primary: true,
    task: 'financial',
  },
  {
    title: 'Efficient Memory Management for Large Language Model Serving with PagedAttention',
    publisher: 'arXiv',
    url: 'https://arxiv.org/abs/2309.06180',
    source_type: 'arxiv',
    published_days_ago: 210,
    excerpt:
      'Introduces paged key-value cache management, the technique underlying much of the current generation of open-source serving stacks.',
    tier: 'reputable',
    is_primary: true,
    task: 'technology',
    author: 'Kwon et al.',
  },
  {
    title: 'vllm-project/vllm repository activity',
    publisher: 'GitHub',
    url: 'https://github.com/vllm-project/vllm',
    source_type: 'github',
    published_days_ago: 2,
    excerpt:
      'Commit cadence, release tags, contributor count and issue volume for the most widely deployed open-source inference server.',
    tier: 'community',
    is_primary: true,
    task: 'technology',
  },
  {
    title: 'Groq describes deterministic low-latency inference architecture',
    publisher: 'Groq',
    url: 'https://groq.com/technology',
    source_type: 'web',
    published_days_ago: 61,
    excerpt:
      'Vendor description of a custom accelerator design optimised for sequential token generation rather than batch training throughput.',
    tier: 'official',
    is_primary: true,
    task: 'technology',
  },
  {
    title: 'Modal pricing and billing granularity',
    publisher: 'Modal',
    url: 'https://modal.com/pricing',
    source_type: 'web',
    published_days_ago: 12,
    excerpt:
      'Per-second billing for GPU compute with cold-start characteristics described per accelerator class.',
    tier: 'official',
    is_primary: true,
    task: 'pricing',
  },
  {
    title: 'AI infrastructure spending outlook',
    publisher: 'Financial Times',
    url: 'https://www.ft.com/ai-infrastructure-outlook',
    source_type: 'web',
    published_days_ago: 27,
    excerpt:
      'Analysis of capital expenditure commitments across hyperscalers and independent providers, with a market sizing estimate.',
    tier: 'reputable',
    is_primary: false,
    task: 'market',
  },
  {
    title: 'Inference market sizing note',
    publisher: 'Reuters',
    url: 'https://www.reuters.com/technology/ai-inference-market',
    source_type: 'web',
    published_days_ago: 19,
    excerpt:
      'Wire coverage citing an alternative market sizing methodology that counts only third-party served inference.',
    tier: 'reputable',
    is_primary: false,
    task: 'market',
  },
  {
    title: 'Replicate model catalogue and cold start behaviour',
    publisher: 'Replicate',
    url: 'https://replicate.com/docs/how-does-replicate-work',
    source_type: 'web',
    published_days_ago: 40,
    excerpt:
      'Documentation of container-based model packaging, catalogue breadth and the trade-off it implies for first-request latency.',
    tier: 'official',
    is_primary: true,
    task: 'competitors',
  },
  {
    title: 'Anyscale positions Ray Serve for hybrid workloads',
    publisher: 'Anyscale',
    url: 'https://www.anyscale.com/blog/ray-serve-production',
    source_type: 'web',
    published_days_ago: 73,
    excerpt:
      'Argument for co-locating training and serving on one scheduler, with reference customer workloads.',
    tier: 'official',
    is_primary: true,
    task: 'competitors',
  },
  {
    title: 'Accelerator supply constraints persist into next cycle',
    publisher: 'The Information',
    url: 'https://www.theinformation.com/accelerator-supply',
    source_type: 'web',
    published_days_ago: 15,
    excerpt:
      'Reporting on allocation dynamics between accelerator vendors and large infrastructure buyers.',
    tier: 'reputable',
    is_primary: false,
    task: 'risk',
  },
  {
    title: 'Cerebras announces wafer-scale inference service',
    publisher: 'Cerebras',
    url: 'https://www.cerebras.net/inference',
    source_type: 'web',
    published_days_ago: 52,
    excerpt:
      'Vendor announcement of a hosted inference offering with published tokens-per-second figures for several open models.',
    tier: 'official',
    is_primary: true,
    task: 'recent-news',
  },
  {
    title: 'Lambda Labs GPU cloud rate card',
    publisher: 'Lambda Labs',
    url: 'https://lambdalabs.com/service/gpu-cloud',
    source_type: 'web',
    published_days_ago: 9,
    excerpt:
      'Hourly on-demand and reserved pricing per accelerator type, the input cost most inference providers build on.',
    tier: 'official',
    is_primary: true,
    task: 'gross-margin',
  },
  {
    title: 'Internal briefing: inference build-versus-buy analysis',
    publisher: 'Uploaded document',
    url: 'upload://inference-build-vs-buy.pdf',
    source_type: 'upload',
    published_days_ago: 4,
    excerpt:
      'User-supplied PDF summarising an internal cost model for self-hosting against managed inference.',
    tier: 'unknown',
    is_primary: true,
    task: 'gross-margin',
  },
  {
    title: 'Speculative decoding throughput results',
    publisher: 'arXiv',
    url: 'https://arxiv.org/abs/2211.17192',
    source_type: 'arxiv',
    published_days_ago: 320,
    excerpt:
      'Reports latency reductions from draft-model speculative decoding under specified batch conditions.',
    tier: 'reputable',
    is_primary: true,
    task: 'technology',
    author: 'Leviathan et al.',
  },
  {
    title: 'huggingface/text-generation-inference release notes',
    publisher: 'GitHub',
    url: 'https://github.com/huggingface/text-generation-inference/releases',
    source_type: 'github',
    published_days_ago: 11,
    excerpt:
      'Release history showing quantisation support and continuous batching improvements over recent versions.',
    tier: 'community',
    is_primary: true,
    task: 'technology',
  },
  {
    title: 'Enterprise buyers consolidate inference vendors',
    publisher: 'Bloomberg',
    url: 'https://www.bloomberg.com/enterprise-ai-vendor-consolidation',
    source_type: 'web',
    published_days_ago: 23,
    excerpt:
      'Survey-based reporting on procurement behaviour, suggesting pressure on providers without a differentiated latency or compliance story.',
    tier: 'reputable',
    is_primary: false,
    task: 'risk',
  },
  {
    title: 'Together AI blog mirror',
    publisher: 'Aggregator',
    url: 'https://news.aggregator.example/together-ai-capacity',
    source_type: 'web',
    published_days_ago: 33,
    excerpt: 'Syndicated copy of the Together AI capacity announcement.',
    tier: 'unknown',
    is_primary: false,
    task: 'recent-news',
  },
];

/** The index of the source that is a near-duplicate of source 0. */
export const DUPLICATE_PAIR = { primary: 0, duplicate: 19 } as const;

export interface ClaimSeed {
  text: string;
  subject: string;
  predicate: string;
  object_value: string;
  claim_type: ClaimType;
  normalized_key: string;
  status: ClaimStatus;
  confidence: number;
  task: string;
  /** Indices into SOURCES; the first is the primary support. */
  supporting: number[];
  refuting?: number[];
  span: string;
  stance?: EvidenceStance;
}

export const CLAIMS: readonly ClaimSeed[] = [
  {
    text: 'Third-party AI inference is the fastest-growing segment of AI infrastructure spend.',
    subject: 'AI inference market',
    predicate: 'growth ranking',
    object_value: 'fastest-growing segment',
    claim_type: 'qualitative',
    normalized_key: 'market.growth.ranking',
    status: 'verified',
    confidence: 0.86,
    task: 'market',
    supporting: [8, 9],
    span: 'independent inference providers account for the fastest-growing share of AI infrastructure spend',
  },
  {
    text: 'Estimates of the served-inference market differ by roughly a factor of two depending on methodology.',
    subject: 'AI inference market',
    predicate: 'market size estimate',
    object_value: 'methodology dependent',
    claim_type: 'quantitative',
    normalized_key: 'market.size.estimate',
    status: 'contested',
    confidence: 0.61,
    task: 'market',
    supporting: [8],
    refuting: [9],
    span: 'the estimate counts only inference served by third parties, excluding in-house deployments',
  },
  {
    text: 'Serverless inference is priced per million tokens, while GPU platforms are priced per accelerator-second.',
    subject: 'Inference pricing',
    predicate: 'billing unit',
    object_value: 'tokens versus accelerator-seconds',
    claim_type: 'qualitative',
    normalized_key: 'pricing.unit.model',
    status: 'verified',
    confidence: 0.94,
    task: 'pricing',
    supporting: [1, 7],
    span: 'billing is per million input and output tokens, charged separately by model tier',
  },
  {
    text: 'Per-token pricing for mid-sized open models varies by more than 3x across providers.',
    subject: 'Inference pricing',
    predicate: 'price dispersion',
    object_value: 'greater than 3x',
    claim_type: 'quantitative',
    normalized_key: 'pricing.dispersion.mid-tier',
    status: 'verified',
    confidence: 0.79,
    task: 'pricing',
    supporting: [1, 7, 14],
    span: 'rates for the same parameter class differ substantially between the published price lists',
  },
  {
    text: 'Paged key-value cache management is the dominant memory technique in current serving stacks.',
    subject: 'Serving stack',
    predicate: 'dominant technique',
    object_value: 'paged KV cache',
    claim_type: 'qualitative',
    normalized_key: 'tech.serving.kv-cache',
    status: 'verified',
    confidence: 0.91,
    task: 'technology',
    supporting: [4, 5, 17],
    span: 'PagedAttention partitions the key-value cache into fixed-size blocks, reducing fragmentation',
  },
  {
    text: 'Speculative decoding reduces end-to-end latency without changing the output distribution.',
    subject: 'Speculative decoding',
    predicate: 'effect',
    object_value: 'latency reduction, distribution preserved',
    claim_type: 'qualitative',
    normalized_key: 'tech.speculative-decoding.effect',
    status: 'verified',
    confidence: 0.88,
    task: 'technology',
    supporting: [16],
    span: 'the sampling procedure provably preserves the target model output distribution',
  },
  {
    text: 'Custom accelerators are positioned on deterministic latency rather than cost per token.',
    subject: 'Custom accelerators',
    predicate: 'positioning',
    object_value: 'deterministic latency',
    claim_type: 'qualitative',
    normalized_key: 'tech.custom-silicon.positioning',
    status: 'verified',
    confidence: 0.74,
    task: 'technology',
    supporting: [6, 13],
    span: 'the architecture is designed for predictable per-token latency rather than aggregate batch throughput',
  },
  {
    text: 'Cold-start latency is the principal trade-off of container-packaged model catalogues.',
    subject: 'Container-based serving',
    predicate: 'primary trade-off',
    object_value: 'cold-start latency',
    claim_type: 'qualitative',
    normalized_key: 'tech.cold-start.tradeoff',
    status: 'verified',
    confidence: 0.83,
    task: 'competitors',
    supporting: [10, 7],
    span: 'a model that has not run recently must be pulled and initialised before the first request completes',
  },
  {
    text: 'At least one major provider raised a growth round in the last twelve months.',
    subject: 'Provider funding',
    predicate: 'recent round',
    object_value: 'growth round raised',
    claim_type: 'event',
    normalized_key: 'financial.funding.recent-round',
    status: 'verified',
    confidence: 0.9,
    task: 'financial',
    supporting: [2],
    span: 'the round was led by an existing investor with participation from new institutional backers',
  },
  {
    text: 'Public filings disclose material customer concentration for at least one listed provider.',
    subject: 'Customer concentration',
    predicate: 'disclosure',
    object_value: 'material concentration disclosed',
    claim_type: 'qualitative',
    normalized_key: 'financial.concentration.disclosure',
    status: 'verified',
    confidence: 0.93,
    task: 'financial',
    supporting: [3],
    span: 'a limited number of customers accounted for a substantial majority of revenue in the period',
  },
  {
    text: 'Capital expenditure on accelerators is the dominant cost line for infrastructure providers.',
    subject: 'Cost structure',
    predicate: 'dominant line item',
    object_value: 'accelerator capex',
    claim_type: 'qualitative',
    normalized_key: 'financial.cost.dominant-line',
    status: 'verified',
    confidence: 0.87,
    task: 'gross-margin',
    supporting: [3, 14],
    span: 'purchases of computing equipment represent the largest component of investing activities',
  },
  {
    text: 'Gross margin on resold inference is structurally capped by underlying GPU rental rates.',
    subject: 'Gross margin',
    predicate: 'constraint',
    object_value: 'capped by GPU rental cost',
    claim_type: 'qualitative',
    normalized_key: 'margin.constraint.gpu-rental',
    status: 'candidate',
    confidence: 0.58,
    task: 'gross-margin',
    supporting: [14, 15],
    span: 'the internal model assumes hourly accelerator cost as the floor on cost of goods sold',
  },
  {
    text: 'Accelerator supply allocation remains a constraint on capacity expansion.',
    subject: 'Supply chain',
    predicate: 'status',
    object_value: 'constrained',
    claim_type: 'qualitative',
    normalized_key: 'risk.supply.allocation',
    status: 'verified',
    confidence: 0.81,
    task: 'risk',
    supporting: [12],
    span: 'allocation decisions continue to favour the largest buyers, limiting availability for smaller providers',
  },
  {
    text: 'Enterprise buyers are consolidating onto fewer inference vendors.',
    subject: 'Enterprise procurement',
    predicate: 'trend',
    object_value: 'vendor consolidation',
    claim_type: 'qualitative',
    normalized_key: 'risk.demand.consolidation',
    status: 'verified',
    confidence: 0.76,
    task: 'risk',
    supporting: [18],
    span: 'respondents reported reducing the number of inference vendors under contract',
  },
  {
    text: 'Open-source serving stacks are converging on a common feature set.',
    subject: 'Open-source serving',
    predicate: 'trend',
    object_value: 'feature convergence',
    claim_type: 'qualitative',
    normalized_key: 'tech.oss.convergence',
    status: 'verified',
    confidence: 0.8,
    task: 'technology',
    supporting: [5, 17],
    span: 'continuous batching and quantisation support now appear across the major open-source servers',
  },
  {
    text: 'Providers differentiate primarily on latency, catalogue breadth, or compliance posture.',
    subject: 'Competitive differentiation',
    predicate: 'axes',
    object_value: 'latency, catalogue, compliance',
    claim_type: 'qualitative',
    normalized_key: 'competitors.differentiation.axes',
    status: 'verified',
    confidence: 0.72,
    task: 'competitors',
    supporting: [10, 11, 6],
    span: 'positioning statements cluster around three axes rather than raw price',
  },
  {
    text: 'A major provider publicly announced an inference capacity expansion this quarter.',
    subject: 'Capacity',
    predicate: 'announcement',
    object_value: 'expansion announced',
    claim_type: 'event',
    normalized_key: 'news.capacity.expansion',
    status: 'verified',
    confidence: 0.89,
    task: 'recent-news',
    supporting: [0, 19],
    span: 'we increased our dedicated inference fleet over the quarter to meet sustained demand',
  },
  {
    text: 'Published tokens-per-second figures are not comparable across vendors without a fixed benchmark.',
    subject: 'Performance claims',
    predicate: 'comparability',
    object_value: 'not directly comparable',
    claim_type: 'qualitative',
    normalized_key: 'tech.benchmark.comparability',
    status: 'verified',
    confidence: 0.85,
    task: 'technology',
    supporting: [13, 6],
    span: 'the figures are reported under vendor-selected batch sizes and sequence lengths',
  },
];

export interface ContradictionSeed {
  normalized_key: string;
  claim_a: number;
  claim_b: number;
  value_a: string;
  value_b: string;
  source_a: number;
  source_b: number;
  likely_reason: string;
  resolution: 'unresolved' | 'resolved_a' | 'resolved_b' | 'both_valid_in_context';
}

export const CONTRADICTIONS: readonly ContradictionSeed[] = [
  {
    normalized_key: 'market.size.estimate',
    claim_a: 1,
    claim_b: 1,
    value_a: 'Larger sizing, includes in-house inference spend',
    value_b: 'Smaller sizing, third-party served inference only',
    source_a: 8,
    source_b: 9,
    likely_reason:
      'Different scope definitions: one estimate includes captive workloads, the other excludes them.',
    resolution: 'both_valid_in_context',
  },
  {
    normalized_key: 'pricing.dispersion.mid-tier',
    claim_a: 3,
    claim_b: 3,
    value_a: 'Rate quoted for serverless per-token billing',
    value_b: 'Rate implied by dedicated per-second billing at stated utilisation',
    source_a: 1,
    source_b: 7,
    likely_reason:
      'The two prices are for different billing models and are only comparable at an assumed utilisation.',
    resolution: 'unresolved',
  },
  {
    normalized_key: 'news.capacity.expansion',
    claim_a: 16,
    claim_b: 16,
    value_a: 'Expansion described in the vendor announcement',
    value_b: 'Syndicated copy omits the qualifying detail',
    source_a: 0,
    source_b: 19,
    likely_reason: 'The aggregator reproduced a partial version of the primary announcement.',
    resolution: 'resolved_a',
  },
];

export interface SectionSeed {
  kind: ReportSectionKind;
  heading: string;
  /** Markdown. `[n]` markers are rewritten to real citation ordinals. */
  content_md: string;
}

export const REPORT_SECTIONS: readonly SectionSeed[] = [
  {
    kind: 'executive_summary',
    heading: 'Executive Summary',
    content_md: `The independent AI inference market is growing quickly, but the providers in it are
converging technically while diverging commercially. Open-source serving stacks have absorbed the
techniques that were differentiators eighteen months ago [c5][c15], so the durable differences are
now latency guarantees, catalogue breadth and compliance posture [c16] rather than raw throughput.

Pricing is the least comparable dimension in the category. Serverless offerings bill per million
tokens while GPU platforms bill per accelerator-second [c3], and the resulting rates for the same
model class differ by more than three times [c4]. Two of the three contradictions this run surfaced
are artefacts of that mismatch rather than genuine disagreements about fact.

The binding constraint on the category is supply and cost, not demand: accelerator allocation still
favours the largest buyers [c13], capital expenditure on accelerators dominates provider cost
structures [c11], and gross margin on resold inference is bounded below by rental rates [c12].`,
  },
  {
    kind: 'key_findings',
    heading: 'Key Findings',
    content_md: `1. **Technical convergence is largely complete.** Paged key-value cache management is
   standard [c5], and continuous batching and quantisation now appear across the major open-source
   servers [c15]. A provider whose pitch is "we run models efficiently" no longer has a moat.
2. **Pricing comparisons are mostly invalid as published.** Billing units differ structurally [c3],
   and cross-provider rate dispersion exceeds 3x for the same parameter class [c4].
3. **Differentiation has moved up the stack** to latency determinism, catalogue breadth and
   compliance [c16][c7].
4. **Cost of goods is the strategic variable.** Accelerator capital expenditure dominates provider
   cost structures [c11], and margin is capped by rental rates [c12].
5. **Demand-side risk is real.** Enterprise buyers are reducing vendor counts [c14], which
   compresses the middle of the market first.`,
  },
  {
    kind: 'detailed_analysis',
    heading: 'Detailed Analysis',
    content_md: `### Market

Independent inference is the fastest-growing line within AI infrastructure spend [c1]. Sizing it is
where the sources stop agreeing: estimates differ by roughly a factor of two depending on whether
in-house inference is counted [c2]. That is a scope disagreement, not an accuracy dispute, and it is
recorded as such rather than averaged away.

### Technology

The serving layer has standardised. Paged key-value cache management is the dominant memory
technique [c5], speculative decoding is understood as a latency optimisation that provably preserves
the output distribution [c6], and the open-source servers have converged on a common feature
set [c15]. Custom silicon is the one genuine architectural divergence, and it is positioned on
deterministic latency rather than cost per token [c7]. Published tokens-per-second figures should
not be compared across vendors without a fixed benchmark, since each is reported under
vendor-selected batch and sequence conditions [c18].

### Competitive landscape

Container-packaged catalogues trade first-request latency for breadth [c8]. Platforms that co-locate
training and serving argue for scheduler consolidation. In practice, positioning statements cluster
on three axes - latency, catalogue, compliance - rather than on price [c16].

### Financial position

At least one major provider closed a growth round in the last twelve months [c9]. Public filings
disclose material customer concentration for at least one listed provider [c10], which is the
clearest quantitative risk signal available in the primary sources.`,
  },
  {
    kind: 'competitive_landscape',
    heading: 'Competitive Landscape',
    content_md: `| Axis | Who leads on it | Evidence strength |
|---|---|---|
| Deterministic latency | Custom-silicon providers [c7] | Vendor-primary, one corroborating source |
| Catalogue breadth | Container-packaged platforms [c8] | Vendor-primary, documented trade-off |
| Unit price transparency | Serverless per-token providers [c3] | Strong: published rate cards |
| Capital depth | Listed and late-stage providers [c9][c10] | Strong: filings and funding coverage |

No provider leads on more than one axis in the evidence gathered. Treat any single-axis lead as a
positioning claim rather than a durable advantage until corroborated by a second primary source.`,
  },
  {
    kind: 'evidence',
    heading: 'Evidence',
    content_md: `Every finding above resolves to a verbatim span in a retrieved document. The
evidence tab lists all extracted claims with their supporting and refuting spans, source, extraction
model and confidence. Claims below the corroboration threshold remain marked *candidate* and are
excluded from the key findings - most notably the margin-cap claim [c12], which rests on one
third-party rate card and one user-supplied document.`,
  },
  {
    kind: 'contradictions',
    heading: 'Contradictions',
    content_md: `Three contradictions were detected and none were silently resolved.

1. **Market sizing** [c2] - two estimates differ by roughly 2x. Likely cause is scope: one counts
   in-house inference, the other does not. Recorded as *both valid in context*.
2. **Mid-tier pricing** [c4] - a per-token rate and a per-accelerator-second rate are not comparable
   without an assumed utilisation. Recorded as *unresolved*; resolving it needs a utilisation
   assumption the sources do not supply.
3. **Capacity announcement** [c17] - a syndicated copy omitted a qualifier present in the primary
   announcement. Resolved in favour of the primary source.`,
  },
  {
    kind: 'confidence_assessment',
    heading: 'Confidence Assessment',
    content_md: `Overall confidence is **moderate-to-high** for the technical and competitive
findings, which rest on primary vendor documentation and peer-reviewed work, and **moderate** for
the financial and market-sizing findings, which depend on secondary reporting and on disclosure that
varies between private and listed providers.

The weakest link is unit economics. The margin-cap conclusion [c12] is supported by one rate card
and one user-supplied document, with no independent corroboration, and is reported as a candidate
claim rather than a finding.`,
  },
  {
    kind: 'recommendations',
    heading: 'Recommendations',
    content_md: `1. **Do not compete on price per token.** Dispersion is wide [c4] and the floor is
   set by accelerator rental cost, which is not controllable at small scale [c12].
2. **Pick one differentiation axis** - latency, catalogue or compliance - and instrument it against
   a fixed public benchmark, since vendor-published throughput figures are not comparable [c18].
3. **Model customer concentration explicitly.** It is the risk the primary filings actually
   disclose [c10], and it compounds with the vendor-consolidation trend [c14].
4. **Re-run this analysis with a utilisation assumption** to close the unresolved pricing
   contradiction; that single input converts the second contradiction into a comparison.`,
  },
];

/** Alternative research questions used for the seeded history. */
export const HISTORY_QUESTIONS: ReadonlyArray<{
  title: string;
  question: string;
  mode: 'quick' | 'deep';
  status: 'completed' | 'failed' | 'cancelled';
  days_ago: number;
  sources: number;
  claims: number;
  contradictions: number;
  cost: number;
}> = [
  {
    title: 'Vector database landscape',
    question:
      'Compare managed vector database providers on pricing, recall benchmarks, operational maturity and lock-in risk.',
    mode: 'deep',
    status: 'completed',
    days_ago: 3,
    sources: 34,
    claims: 41,
    contradictions: 2,
    cost: 1.42,
  },
  {
    title: 'Agent evaluation practice',
    question: 'What evaluation methodologies are used for multi-step LLM agents in production?',
    mode: 'deep',
    status: 'completed',
    days_ago: 6,
    sources: 28,
    claims: 33,
    contradictions: 1,
    cost: 1.18,
  },
  {
    title: 'EU AI Act obligations',
    question:
      'What obligations does the EU AI Act place on providers of general-purpose AI models?',
    mode: 'quick',
    status: 'completed',
    days_ago: 8,
    sources: 9,
    claims: 12,
    contradictions: 0,
    cost: 0.21,
  },
  {
    title: 'Retrieval chunking strategies',
    question:
      'Which chunking strategies measurably improve retrieval recall on technical documents?',
    mode: 'deep',
    status: 'failed',
    days_ago: 11,
    sources: 6,
    claims: 4,
    contradictions: 0,
    cost: 0.34,
  },
  {
    title: 'Observability tooling for LLM apps',
    question: 'Compare tracing and evaluation tooling for LLM applications on coverage and cost.',
    mode: 'deep',
    status: 'cancelled',
    days_ago: 14,
    sources: 11,
    claims: 7,
    contradictions: 0,
    cost: 0.29,
  },
];
