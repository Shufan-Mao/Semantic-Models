from pathlib import Path
from typing import List, Tuple
import pandas as pd
import random
import numpy as np

from missingadjunct.corpus import Corpus
from missingadjunct.utils import make_blank_sr_df

from src.utils import calc_sr_cores_from_spatial_model
from src.params import Params
from src.other_dsms.count import CountDSM
from src.other_dsms.w2vec import W2Vec
from src.other_dsms.rnn import RNN
from src.other_dsms.transformer import Transformer
from src.networks.ctn import CTN
from src.networks.lon import LON

p2val = {'dsm':'ctn',
         'save_path':'Data',
         'excluded_tokens':None,
         'include_location':False,
        'include_location_specific_agents':False,
        'num_blocks':400,
        'complete_block':True,
        'add_with':True,
        'add_in':True,
        'strict_compositional':False,
        'add_reversed_seq':False,
        'composition_fn': 'native'

         }
decay = 0.75
step_bound = None # non-recurrent activation if None, recurrent activation with bound if certain number


def main(param2val):
    """
    Train a single DSM once, and save results
    """

    # params
    params = Params.from_param2val(param2val)
    print(params)

    save_path = Path(param2val['save_path'])

    # in case job is run locally, we must create save_path
    if not save_path.exists():
        save_path.mkdir(parents=True)

    # TODO: in newer version of MissingAdjunct, Corpus class has more arguments

    corpus = Corpus(include_location=params.corpus_params.include_location,
                    include_location_specific_agents=params.corpus_params.include_location_specific_agents,
                    num_epochs=params.corpus_params.num_blocks,
                    #complete_epoch=params.corpus_params.complete_block,
                    seed=random.randint(0, 1000),
                    #add_with=params.corpus_params.add_with,
                    #add_in=params.corpus_params.add_in,
                    #strict_compositional=params.corpus_params.strict_compositional,
                    )

    # load blank df for evaluating sr scores
    df_blank = make_blank_sr_df()
    df_blank.insert(loc=3, column='location-type', value=['' for i in range(df_blank.shape[0])])
    df_results = df_blank.copy()
    instruments = df_blank.columns[4:]  # instrument columns start after the 4th column
    if not set(instruments).issubset(corpus.vocab):
        raise RuntimeError('Not all instruments in corpus. Add more blocks or set complete_block=True')

    # collect corpus data
    seq_num: List[List[int]] = []  # sequences of Ids
    seq_tok: List[List[str]] = []  # sequences of tokens
    seq_parsed: List[Tuple] = []  # sequences that are constituent-parsed

    # TODO: in newer version of MissingAdjunct, token2id, eos are attributes of the Corpus class
    token2id = {t: n for n, t in enumerate(corpus.vocab)}
    eos = '<eos>'

    for s in corpus.get_sentences():  # a sentence is a string
        tokens = s.split()
        seq_num.append([token2id[token] for token in tokens])  # numeric (token IDs)
        seq_tok.append(tokens)  # raw tokens
        if params.corpus_params.add_reversed_seq:
            seq_num.append([token2id[token] for token in tokens][::-1])
            seq_tok.append(tokens[::-1])
    for tree in corpus.get_trees():
        seq_parsed.append(tree)

    print(f'Number of sequences in corpus={len(seq_tok):,}', flush=True)

    if params.dsm == 'count':
        dsm = CountDSM(params.dsm_params, corpus.vocab, seq_num)
    elif params.dsm == 'w2v':
        dsm = W2Vec(params.dsm_params, corpus.vocab, seq_tok)
    elif params.dsm == 'rnn':
        dsm = RNN(params.dsm_params, token2id, seq_num, df_blank, instruments, save_path)
    elif params.dsm == 'transformer':
        dsm = Transformer(params.dsm_params, token2id, seq_num, df_blank, instruments, save_path, eos)
    elif params.dsm == 'ctn':
        dsm = CTN(params.dsm_params, token2id, seq_parsed, decay)

    elif params.dsm == 'lon':
        dsm = LON(params.dsm_params, seq_tok, decay)  # TODO the net is built directly from corpus rather than co-occ
    else:
        raise NotImplementedError


    # train
    dsm.train()
    print(f'Completed training the DSM', flush=True)

    if params.dsm == 'ctn' or params.dsm == 'lon':
        dsm.get_accumulated_activations()

    # fill in blank data frame with semantic-relatedness scores
    for verb_phrase, row in df_blank.iterrows():
        verb, theme = verb_phrase.split()

        # score graphical models
        if isinstance(dsm, LON) or isinstance(dsm, CTN):
            scores = dsm.calc_sr_scores(verb, theme, instruments, step_bound)

        # score spatial models
        else:
            if params.composition_fn == 'native':  # use next-word prediction to compute sr scores
                scores = dsm.calc_native_sr_scores(verb, theme, instruments)
            else:
                scores = calc_sr_cores_from_spatial_model(dsm, verb, theme, instruments, params.composition_fn)

        # collect sr scores in new df
        df_results.loc[verb_phrase] = [row['verb-type'],
                                       row['theme-type'],
                                       row['phrase-type'],
                                       row['location-type']
                                       ] + scores

    df_results.to_csv(save_path / 'df_sr.csv')

    # prepare collected data for returning to Ludwig
    performance = dsm.get_performance()
    series_list = []
    for k, v in performance.items():
        if k == 'epoch':
            continue
        s = pd.Series(v, index=performance['epoch'])
        s.name = k
        series_list.append(s)

    # save model
    if isinstance(dsm, Transformer):
        dsm.model.save_pretrained(str(save_path))

    print('Completed main.job.', flush=True)

    return series_list

main(p2val)