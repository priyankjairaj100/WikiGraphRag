"""Shared decoding with explicit correction certificates and support withdrawal."""
from dataclasses import dataclass
from math import fsum, isclose

from .correction_io import FrozenCorrectionInput, correction_cache_digest
from .corrections import make_correction_state_semantics, materialize_corrected_selection
from .decoder import Limits, Result, decode_independent, decode_iterative, decode_joint
from .representation_io import answer_question
from .coupled import CoupledMemory


@dataclass(frozen=True)
class CorrectedState:
    cache_digest: str
    method: str
    result: Result
    memory: CoupledMemory


def _objective_parts(base, result, memory):
    """Expose scores retained for historical assertions and deactivated links."""
    weights={p.name:p.weight for p in base.problem.penalties}
    selected=dict(result.reading_ids); links=dict(result.link_ids)
    link_map={link.link_id:link for link in base.problem.links}
    active={claim.claim_id for claim in memory.claims}
    active_links=set(memory.diagnostics['correction_ledger']['active_temporal_link_ids'])
    parts={name:[] for name in ('active_claim_unary_net','inactive_claim_unary_net',
        'unresolved_unary_net','active_temporal_link_net','inactive_temporal_link_net',
        'correction_link_net','null_link_score')}
    for mention in base.problem.mentions:
        reading=next(r for r in mention.readings if r.reading_id==selected[mention.mention_id])
        if reading.unresolved:kind='unresolved_unary_net'
        else:
            claim=base.claims[mention.mention_id,reading.reading_id]
            kind='active_claim_unary_net' if claim.claim_id in active else 'inactive_claim_unary_net'
        parts[kind].append(reading.unary_score-fsum(weights[v] for v in reading.violations))
        lid=links[mention.mention_id]
        if lid is None:parts['null_link_score'].append(mention.null_score)
        else:
            link=link_map[lid]
            kind=('correction_link_net' if link.relation=='CORRECTS' else
                  'active_temporal_link_net' if lid in active_links else 'inactive_temporal_link_net')
            parts[kind].append(base.problem.beta*link.score-fsum(weights[v] for v in link.violations))
    totals={name:fsum(values) for name,values in parts.items()}
    if not isclose(fsum(totals.values()),result.objective,rel_tol=1e-12,abs_tol=1e-9):
        raise ValueError('Objective decomposition does not match decoder objective')
    return {**totals,'total':result.objective,
        'interpretation':'Shared historical-interpretation objective retains inactive reading/link scores; not an active-answer utility.'}


def decode_correction_cache(cache: FrozenCorrectionInput, method: str, *,
                            limits: Limits = Limits(), restarts: int = 10,
                            seed: int = 7) -> CorrectedState:
    if correction_cache_digest(cache) != cache.digest:
        raise ValueError('Correction cache digest mismatch')
    base=cache.base
    semantics=make_correction_state_semantics(base.problem,base.claims,cache.policy)
    if method=='independent':
        result=decode_independent(base.problem,limits,state_semantics=semantics)
    elif method in {'iterative','iterative_restarts'}:
        result=decode_iterative(base.problem,limits,state_semantics=semantics,
            restarts=restarts if method=='iterative_restarts' else 0,seed=seed)
    elif method=='joint_exact':
        result=decode_joint(base.problem,limits,state_semantics=semantics)
    else:
        raise ValueError('Unknown decoder method')
    memory=materialize_corrected_selection(base.problem,dict(result.reading_ids),
        dict(result.link_ids),base.claims,cache.policy)
    memory.diagnostics['objective_decomposition']=_objective_parts(base,result,memory)
    return CorrectedState(cache.digest,method,result,memory)


def answer_corrected(cache: FrozenCorrectionInput, state: CorrectedState, question, *,
                      query_mode='announced_schedule', use_aliases=True):
    if correction_cache_digest(cache)!=state.cache_digest or cache.digest!=state.cache_digest:
        raise ValueError('Decoded state does not belong to this correction cache')
    base=cache.base
    if question.information_cutoff!=base.problem.cutoff:
        raise ValueError('Question cutoff must match decoded prefix')
    prediction=answer_question(state.memory,question,state.memory.claims,base.aliases,
        base.problem.sources,query_mode=query_mode,use_aliases=use_aliases)
    prediction.diagnostics.update({
        'shared_correction_cache_sha256':cache.digest,
        'shared_base_cache_sha256':base.digest,
        'decoder_method':state.method,
        'selected_objective':state.result.objective,
        'materializer':'targeted_assertion_replacement_v0.5',
        'correction_semantics_shared_across_methods':True,
        'certainty_scope':'conditional_on_selected_readings_links_and_correction_certificates',
        'alternative_state_consensus_checked':False,
        'authority_certificates_independently_verified':False,
    })
    return prediction
