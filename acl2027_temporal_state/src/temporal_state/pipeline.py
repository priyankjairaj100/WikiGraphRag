"""Question-free decoding followed by common, source-routed temporal answering.

The output is conditional on one selected discrete interpretation. Exhaustive
score optimization does not certify factual truth or agreement across ties.
"""
from dataclasses import dataclass

from .coupled import CoupledMemory, make_feasibility_check, materialize_selection
from .decoder import Limits, Result, decode_independent, decode_iterative, decode_joint
from .representation_io import answer_question
from .scored_io import FrozenScoredInput, cache_digest


METHODS = ('independent', 'iterative', 'iterative_restarts', 'joint_exact')


@dataclass(frozen=True)
class DecodedState:
    cache_digest: str
    method: str
    result: Result
    memory: CoupledMemory


def decode_cache(cache: FrozenScoredInput, method: str, *, limits: Limits = Limits(),
                 restarts: int = 10, seed: int = 7) -> DecodedState:
    """Every method sees identical candidates, local/pair costs and constraints."""
    if cache_digest(cache) != cache.digest:
        raise ValueError('Scored cache digest mismatch')
    check = make_feasibility_check(cache.problem, cache.claims)
    if method == 'independent':
        result = decode_independent(cache.problem, limits, feasibility_check=check)
    elif method in {'iterative', 'iterative_restarts'}:
        result = decode_iterative(cache.problem, limits, feasibility_check=check,
                                  restarts=restarts if method == 'iterative_restarts' else 0,
                                  seed=seed)
    elif method == 'joint_exact':
        result = decode_joint(cache.problem, limits, feasibility_check=check)
    else:
        raise ValueError('Unknown decoder method')
    memory = materialize_selection(cache.problem, dict(result.reading_ids),
                                   dict(result.link_ids), cache.claims)
    return DecodedState(cache.digest, method, result, memory)


def answer_decoded(cache: FrozenScoredInput, state: DecodedState, question, *,
                   query_mode='announced_schedule', use_aliases=True):
    """Route using selected inferred keys and eligible aliases, never gold IDs."""
    if cache_digest(cache) != state.cache_digest or cache.digest != state.cache_digest:
        raise ValueError('Decoded state does not belong to this scored cache')
    if question.information_cutoff != cache.problem.cutoff:
        raise ValueError('Question cutoff must match the decoded prefix')
    prediction = answer_question(state.memory, question, state.memory.claims,
        cache.aliases, cache.problem.sources, use_aliases=use_aliases, query_mode=query_mode)
    prediction.diagnostics.update({
        'shared_cache_sha256': state.cache_digest,
        'decoder_method': state.method,
        'selected_objective': state.result.objective,
        'temporal_semantics_shared_across_methods': True,
        'certainty_scope': 'conditional_on_one_selected_discrete_state',
        'alternative_state_consensus_checked': False,
        'score_optimality_is_factual_truth': False,
    })
    return prediction
