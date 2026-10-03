"""Matched objective ablations over one immutable candidate/correction cache."""
from dataclasses import dataclass
from hashlib import sha256
import json

from .correction_io import FrozenCorrectionInput, correction_cache_digest
from .corrections import make_correction_state_semantics, materialize_corrected_selection
from .coupled import CoupledMemory
from .decoder import Limits, Result, decode_independent, decode_iterative, decode_joint
from .objectives import make_objective_function, objective_breakdown, objective_spec
from .representation_io import answer_question


def objective_identity(cache_digest, mode):
    payload = {'correction_cache_sha256': cache_digest, 'objective': objective_spec(mode)}
    return sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'),
                             allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class ObjectiveState:
    cache_digest: str
    objective_digest: str
    objective_mode: str
    method: str
    result: Result
    memory: CoupledMemory


def decode_objective(cache: FrozenCorrectionInput, method: str, *,
                     objective_mode='historical', limits: Limits = Limits(),
                     restarts=10, seed=7):
    if correction_cache_digest(cache) != cache.digest:
        raise ValueError('Correction cache digest mismatch')
    base = cache.base
    semantics = make_correction_state_semantics(base.problem, base.claims, cache.policy)
    objective = make_objective_function(base.problem, mode=objective_mode)
    kwargs = {'state_semantics': semantics, 'objective_function': objective}
    if method == 'independent':
        result = decode_independent(base.problem, limits, **kwargs)
    elif method in {'iterative', 'iterative_restarts'}:
        result = decode_iterative(base.problem, limits, **kwargs,
            restarts=restarts if method == 'iterative_restarts' else 0, seed=seed)
    elif method == 'joint_exact':
        result = decode_joint(base.problem, limits, **kwargs)
    else:
        raise ValueError('Unknown decoder method')
    memory = materialize_corrected_selection(base.problem, dict(result.reading_ids),
        dict(result.link_ids), base.claims, cache.policy)
    by_mention = {m.mention_id: m for m in base.problem.mentions}
    selected = {mid: next(r for r in by_mention[mid].readings if r.reading_id == rid)
                for mid, rid in result.reading_ids}
    by_link = {link.link_id: link for link in base.problem.links}
    chosen = tuple(by_link[lid] if lid else None for mid, lid in result.link_ids)
    breakdown = objective_breakdown(base.problem, selected, chosen,
        result.restatement_components, mode=objective_mode)
    from math import isclose
    if not isclose(breakdown['total'], result.objective, rel_tol=1e-12, abs_tol=1e-9):
        raise ValueError('Objective audit disagrees with decoder selection')
    memory.diagnostics['objective_ablation'] = breakdown
    memory.diagnostics['objective_spec'] = objective_spec(objective_mode)
    return ObjectiveState(cache.digest, objective_identity(cache.digest, objective_mode),
                          objective_mode, method, result, memory)


def answer_objective(cache, state, question, *, query_mode='announced_schedule',
                     use_aliases=True):
    if (cache.digest != state.cache_digest or correction_cache_digest(cache) != state.cache_digest
            or objective_identity(cache.digest, state.objective_mode) != state.objective_digest):
        raise ValueError('Decoded state does not belong to this cache and objective')
    if question.information_cutoff != cache.base.problem.cutoff:
        raise ValueError('Question cutoff must match decoded prefix')
    prediction = answer_question(state.memory, question, state.memory.claims,
        cache.base.aliases, cache.base.problem.sources,
        query_mode=query_mode, use_aliases=use_aliases)
    prediction.diagnostics.update({
        'shared_correction_cache_sha256': state.cache_digest,
        'objective_identity_sha256': state.objective_digest,
        'objective_mode': state.objective_mode, 'decoder_method': state.method,
        'selected_objective': state.result.objective,
        'certainty_scope': 'conditional_on_one_selected_discrete_state',
        'alternative_state_consensus_checked': False,
        'score_optimality_is_factual_truth': False,
    })
    return prediction
