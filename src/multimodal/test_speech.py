"""Testing script for speech models.

Author: Ryan Eloff
Contact: ryan.peter.eloff@gmail.com
Date: August 2018
"""


from __future__ import absolute_import
from __future__ import division
from __future__ import print_function


import os
import sys
import json
import logging
import argparse
import datetime


import matplotlib
matplotlib.use('Agg')  # use anitgrain rendering engine backend for non-GUI interfaces
from sklearn import preprocessing
import numpy as np
import tensorflow as tf


# Add upper-level 'src' directory to application path
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))


#pylint: disable=E0401
from dtw import speech_dtw
from mltoolset import data
from mltoolset import nearest_neighbour
from mltoolset import utils
from mltoolset import TF_FLOAT, TF_INT
#pylint: enable=E0401
import speech


# Static names for stored log and param files
MODEL_PARAMS_STORE_FN = 'model_params.json'
LOG_FILENAME = 'test_speech.log'


# TIDigits 'words' that appear in Flickr-Audio corpus and should be removed
TIDIGITS_INTERSECTION = [
    'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 
    'oh', 'zero', 'fives', 'fours', 'seventh']


def check_arguments():
    """Check command line arguments for `python test_speech.py`."""
    parser = argparse.ArgumentParser(description=__doc__.strip().split('\n')[0])

    # --------------------------------------------------------------------------
    # General script options (model type, storage, etc.):
    # --------------------------------------------------------------------------
    parser.add_argument('--model-dir', 
                        type=os.path.abspath,
                        help="Path to a trained speech model directory",
                        required=True)
    parser.add_argument('--data-dir', 
                        type=os.path.abspath,
                        help="Path to store and load data"
                             "(defaults to '{}' in the current working "
                             "directory)".format('data'),
                        default='data')
    parser.add_argument('--output-dir', 
                        help="Path to store vision test results "
                             "(defaults to the model directory)",
                        default=None)
    parser.add_argument('--params-file', 
                        type=str,
                        help="Filename of model parameters file e.g. '{0}' "
                             "(defaults to '{0}' in model directory "
                             "if found, else base model parameters "
                             "are used)".format(MODEL_PARAMS_STORE_FN),
                        default=None)
    parser.add_argument('--restore-checkpoint', 
                        type=str,
                        help="Filename of model checkpoint to restore and "
                             "test (defaults to None which uses best model)",
                        default=None)
    parser.add_argument('-rs', '--random-seed', 
                        type=int,
                        help="Random seed (default: 42)",
                        default=42)
    
    # --------------------------------------------------------------------------
    # Model and data pipeline options:
    # --------------------------------------------------------------------------
    parser.add_argument('--test-set',
                        type=str,
                        help="Dataset to use for few-shot speech testing "
                             "(defaults to '{}')".format('tidigits'),
                        choices=['tidigits', 'flickr-audio'],
                        default='tidigits')
    parser.add_argument('--l-way',
                        type=int,
                        help="Number of L unique classes used in few-shot "
                             "evaluation (defaults to {})".format(10),
                        default=10)
    parser.add_argument('--k-shot',
                        type=int,
                        help="Number of K examples sampled per L-way label "
                             "used in few-shot evaluation (defaults to {})"
                             "".format(1),
                        default=1)
    parser.add_argument('--n-queries',
                        type=int,
                        help="Number of N_queries query examples to sample per "
                             "few-shot episode (defaults to {})".format(10),
                        default=10)
    parser.add_argument('--n-test-episodes',
                        type=int,
                        help="Number of few-shot test episodes"
                             "(defaults to {})".format(400),
                        default=400)
    parser.add_argument('--originator-type',
                        type=str,
                        help="Speaker selection in test episodes (defaults to "
                             "'different' where support and query set speakers "
                             "are different; 'same' tests speaker invariance, "
                             "with query speakers always in support set either "
                             "for matching concept (easy) or different concept "
                             "(distractor))",
                        choices=['different', 'same', 'difficult'],
                        default='different')
    return parser.parse_args()


def main():
    # --------------------------------------------------------------------------
    # Parse script args and handle options:
    # --------------------------------------------------------------------------
    ARGS = check_arguments()
    
    # Set numpy and tenorflow random seed
    np.random.seed(ARGS.random_seed)
    tf.set_random_seed(ARGS.random_seed)

    # Get specified model directory (default cwd)
    model_dir = ARGS.model_dir
    test_model_dir = ARGS.output_dir
    if test_model_dir is None:
        test_model_dir = model_dir
    else:
        test_model_dir = os.path.abspath(test_model_dir)
    
    # Check if not using a previous run, and create a unique run directory
    if not os.path.exists(os.path.join(test_model_dir, LOG_FILENAME)):
        unique_dir = "{}_{}".format(
            'speech_test', 
            datetime.datetime.now().strftime("%y%m%d_%Hh%Mm%Ss_%f"))
        test_model_dir = os.path.join(test_model_dir, unique_dir)
    
    # Create directories
    if not os.path.exists(test_model_dir):
        os.makedirs(test_model_dir)
    
    # Set logging to print to console and log to file
    utils.set_logger(test_model_dir, log_fn=LOG_FILENAME)
    logging.info("Using model directory: {}".format(model_dir))

    # Load JSON model params from specified file or a previous run if available
    model_params_store_fn = os.path.join(model_dir, MODEL_PARAMS_STORE_FN)
    if ARGS.params_file is not None:
        params_file = os.path.join(model_dir, ARGS.params_file)
        if not os.path.exists(params_file):
            logging.info("Could not find specified model parameters file: "
                         "{}.".format(params_file))
            return  # exit ...
        else:
            logging.info("Using stored model parameters file: "
                         "{}".format(params_file))
    elif os.path.exists(model_params_store_fn):
        params_file = model_params_store_fn
        logging.info("Using stored model parameters file: "
                     "{}".format(params_file))
    else:
        logging.info("Model parameters file {} could not be found!"
                     "".format(model_params_store_fn))
        return  # exit ...

    # Load JSON into a model params dict 
    try:
        with open(params_file, 'r') as fp:
            model_params = json.load(fp)
        logging.info("Successfully loaded JSON model parameters!")
        logging.info("Testing speech model: version={}".format(
            model_params['model_version']))
    except json.JSONDecodeError as ex:
        logging.info("Could not read JSON model parameters! "
                        "Caught exception: {}".format(ex))
        return  # exit ...
    
    # Read and write testing options from specified/default args
    test_options = {}
    var_args = vars(ARGS)
    for arg in var_args:
        test_options[arg] = getattr(ARGS, arg)
    logging.info("Testing parameters: {}".format(test_options))
    test_options_path = os.path.join(test_model_dir, 'test_options.json')
    with open(test_options_path, 'w') as fp:
        logging.info("Writing most recent testing parameters to file: {}"
                        "".format(test_options_path))
        json.dump(test_options, fp, indent=4)

    # --------------------------------------------------------------------------
    # Get additional model parameters:
    # --------------------------------------------------------------------------
    feats_type = model_params['feats_type']
    n_padding = model_params['n_padded']
    center_padded = model_params['center_padded']
    n_filters = 39 if (feats_type == 'mfcc') else 40

    if n_padding is None or model_params['model_version'] == 'dtw':
        n_padding = 110  # pad to longest segment length in TIDigits (DTW)
        center_padded = False

    # --------------------------------------------------------------------------
    # Load test dataset:
    # --------------------------------------------------------------------------
    if ARGS.test_set == 'flickr-audio':  # load Flickr-Audio test set
        logging.info("Testing speech model on dataset: {}".format('flickr-audio'))
        flickr_data = data.load_flickraudio(
            path=os.path.join(ARGS.data_dir, 'flickr_audio.npz'),
            feats_type=feats_type,
            remove_labels=TIDIGITS_INTERSECTION)  # remove digit words from flickr
        test_data = flickr_data[2]
        
    else:  # load TIDigits (default) test set
        logging.info("Testing speech model on dataset: {}".format('tidigits'))
        tidigits_data = data.load_tidigits(
            path=os.path.join(ARGS.data_dir, 'tidigits_audio.npz'),
            feats_type=feats_type)
        test_data = tidigits_data[2]

    # --------------------------------------------------------------------------
    # Data processing pipeline (placed on CPU so GPU is free):
    # --------------------------------------------------------------------------
    with tf.device('/cpu:0'):
        # --------------------------------------------
        # Create few-shot test dataset pipeline:
        # --------------------------------------------
        x_test = test_data[0]
        y_test = test_data[1]
        z_test = test_data[2]
        x_test_placeholder = tf.placeholder(TF_FLOAT, 
            shape=[None, n_filters, n_padding])
        y_test_placeholder = tf.placeholder(tf.string, shape=[None])
        z_test_placeholder = tf.placeholder(tf.string, shape=[None])
        # Preprocess speech data
        x_test = data.pad_sequences(x_test,
                                    n_padding,
                                    center_padded=center_padded)
        x_test = np.swapaxes(x_test, 2, 1)  # swap to (n_filters, n_pad)
        # Add single depth channel to feature image so it is a 'grayscale image'
        x_test_with_depth= tf.expand_dims(x_test_placeholder, axis=-1)
        # Split data into disjoint support and query sets
        x_test_split, y_test_split, z_test_split = (
            data.make_train_test_split(x_test_with_depth,
                                       y_test_placeholder,
                                       z_test_placeholder,
                                       test_ratio=0.5,
                                       shuffle=True,
                                       seed=ARGS.random_seed))
        # Batch episodes of support and query sets for few-shot validation
        test_pipeline = (
            data.batch_few_shot_episodes(x_support_data=x_test_split[0],
                                         y_support_labels=y_test_split[0],
                                         z_support_originators=z_test_split[0],
                                         x_query_data=x_test_split[1],
                                         y_query_labels=y_test_split[1],
                                         z_query_originators=z_test_split[1],
                                         originator_type=ARGS.originator_type,
                                         k_shot=ARGS.k_shot,
                                         l_way=ARGS.l_way,
                                         n_queries=ARGS.n_queries,
                                         seed=ARGS.random_seed))
        test_pipeline = test_pipeline.prefetch(1)  # prefetch 1 batch per step

        # Create pipeline iterator
        test_iterator = test_pipeline.make_initializable_iterator()
        test_feed_dict = {
            x_test_placeholder: x_test,
            y_test_placeholder: y_test,
            z_test_placeholder: z_test
        }

    # --------------------------------------------------------------------------
    # Build, train, and validate model:
    # --------------------------------------------------------------------------
    # Build selected model version from base/loaded model params dict
    model_embedding, embed_input, train_flag, _, _ = (
        speech.build_speech_model(model_params, training=False))
    # Build few-shot 1-Nearest Neighbour memory comparison model
    query_input = tf.placeholder(TF_FLOAT, shape=[None, None])
    support_memory_input = tf.placeholder(TF_FLOAT, shape=[None, None])
    model_nn_memory = nearest_neighbour.fast_knn_cos(q_batch=query_input,
                                                     m_keys=support_memory_input,
                                                     k_nn=1,
                                                     normalize=True)
    # Check if test using Dynamic Time Warping instead of the memory model above
    test_dtw = False
    dtw_cost_func = None
    dtw_post_process = None
    if model_params['model_version'] == 'dtw':
        test_dtw = True
        dtw_cost_func = speech_dtw.multivariate_dtw_cost_cosine
        dtw_post_process = lambda x: np.ascontiguousarray(  # as cython C-order
            np.swapaxes(  # time on x-axis for DTW
                _get_unpadded_image(np.squeeze(x, axis=-1), n_padding), 1, 0),
            dtype=float)
    # Test the few-shot model ...
    test_few_shot_model(# Test params:
                        train_flag=train_flag,
                        test_iterator=test_iterator,
                        test_feed_dict=test_feed_dict,
                        model_embedding=model_embedding,
                        embed_input=embed_input,
                        query_input=query_input,
                        support_memory_input=support_memory_input,
                        nearest_neighbour=model_nn_memory,
                        n_episodes=ARGS.n_test_episodes,
                        test_dtw=test_dtw,
                        dtw_cost_func=dtw_cost_func,
                        dtw_post_process=dtw_post_process,
                        test_invariance=(ARGS.originator_type == 'same' or ARGS.originator_type == 'difficult'),
                        # Other params:
                        log_interval=int(ARGS.n_test_episodes/10),
                        model_dir=model_dir,
                        output_dir=test_model_dir,
                        summary_dir='summaries/test',
                        restore_checkpoint=ARGS.restore_checkpoint)


def test_few_shot_model(
        train_flag,
        test_iterator,
        test_feed_dict,
        model_embedding,
        embed_input,
        query_input,
        support_memory_input,
        nearest_neighbour,
        n_episodes,
        test_dtw=False,
        dtw_cost_func=None,
        dtw_post_process=None,
        test_invariance=False,
        log_interval=1,
        model_dir='saved_models',
        output_dir='.',
        summary_dir='summaries/test',
        restore_checkpoint=None):
    # Get the global step tensor and set intial step value
    global_step = tf.train.get_or_create_global_step()
    step = 0
    # Define a saver to load model checkpoint
    checkpoint_saver = tf.train.Saver(save_relative_paths=True)
    # Start tf.Session to test model
    with tf.Session() as sess:
        # ----------------------------------------------------------------------
        # Load model (unless using DTW) and log some debug info:
        # ----------------------------------------------------------------------
        if not test_dtw:
            try:  # restore from model checkpoint
                if restore_checkpoint is not None:  # use specific checkpoint
                    restore_path = os.path.join(
                        model_dir, 'checkpoints', restore_checkpoint)
                    if not os.path.isfile('{}.index'.format(restore_path)):
                        restore_path = restore_checkpoint  # possibly full path?
                else:  # use best model if available
                    final_model_dir = os.path.join(model_dir, 'final_model')
                    restore_path = tf.train.latest_checkpoint(final_model_dir)
                    if restore_path is None:
                        logging.info("No best model checkpoint could be found "
                                    "in directory: {}".format(final_model_dir))
                        return  # exit ... 
                checkpoint_saver.restore(sess, restore_path)
                logging.info("Model restored from checkpoint file: "
                             "{}".format(restore_path))
            except ValueError:  # no checkpoints, inform and exit ...
                logging.info("Model checkpoint could not found at restore "
                            "path: {}".format(restore_path))
                return  # exit ... 
        
            # Evaluate global step model was trained to
            step = sess.run(global_step)
            logging.info("Testing from: Global Step: {}"
                        .format(step))
        else:
            logging.info("Testing speech model with dynamic time warping (DTW).")

        # Create session summary writer
        summary_writer = tf.summary.FileWriter(os.path.join(
            output_dir, summary_dir,
            datetime.datetime.now().strftime("%Hh%Mm%Ss_%f")), sess.graph)
        # Get tf.summary tensor to evaluate for few-shot accuracy
        test_acc_input = tf.placeholder(TF_FLOAT)
        test_summ = tf.summary.scalar('test_few_shot_accuracy', test_acc_input)
        # Get support/query few-shot set, and display one episode on tensorboard
        support_set, query_set = test_iterator.get_next()
        sess.run(test_iterator.initializer, feed_dict=test_feed_dict)
        s_summ = tf.summary.image('support_set_images', support_set[0], 10)
        q_summ = tf.summary.image('query_set_images', query_set[0], 10)
        support_batch, query_batch, s_images, q_images = sess.run(
            [support_set, query_set, s_summ, q_summ])
        summary_writer.add_summary(s_images, step)
        summary_writer.add_summary(q_images, step)
        summary_writer.flush()
        # Save figures to pdf for later use ...
        for index, (image, label, speaker) in enumerate(zip(*support_batch)):
            if test_dtw:
                image = dtw_post_process(image)
            else:
                image = np.squeeze(image, axis=-1)
            utils.save_image(image, filename=os.path.join(
                output_dir, 'test_images', '{}_{}_{}_{}_{}_{}.pdf'.format(
                    'support', index, 'label', label.decode("utf-8"), 'speaker',
                    speaker.decode("utf-8"))), cmap='inferno')
        for index, (image, label, speaker) in enumerate(zip(*query_batch)):
            if test_dtw:
                image = dtw_post_process(image)
            else:
                image = np.squeeze(image, axis=-1)
            utils.save_image(image, filename=os.path.join(
                output_dir, 'test_images', '{}_{}_{}_{}_{}_{}.pdf'.format(
                    'query', index, 'label', label.decode("utf-8"), 'speaker',
                    speaker.decode("utf-8"))), cmap='inferno')

        # ----------------------------------------------------------------------
        # Few-shot testing:
        # ----------------------------------------------------------------------
        # Few-shot accuracy counters
        total_queries = 0
        total_correct = 0
        # Speaker invariance accuracy counters
        total_easy_queries = 0
        total_easy_correct = 0
        total_distractor_queries = 0
        total_distractor_correct = 0
        sess.run(test_iterator.initializer, feed_dict=test_feed_dict)
        for episode in range(n_episodes):
            support_batch, query_batch = sess.run([support_set, query_set])
            # Get embeddings and classify queries with 1-NN on support set
            support_embeddings = sess.run(model_embedding, 
                feed_dict={embed_input: support_batch[0], train_flag: False})
            query_embeddings = sess.run(model_embedding, 
                feed_dict={embed_input: query_batch[0], train_flag: False})
            if not test_dtw:  # test with fast cosine 1-NN memory model
                nearest_neighbour_indices = sess.run(nearest_neighbour,
                    feed_dict={query_input: query_embeddings,
                                support_memory_input: support_embeddings})
            else:  # test with dynamic time warping
                costs = [[dtw_cost_func(dtw_post_process(query_embeddings[i]),
                                        dtw_post_process(support_embeddings[j]),
                                        True)
                          for j in range(len(support_embeddings))]
                         for i in range(len(query_embeddings))]
                nearest_neighbour_indices = [
                    np.argmin(costs[i]) for i in range(len(costs))]

            # Calculate and store number of correct predictions
            predicted_labels = support_batch[1][nearest_neighbour_indices] 
            total_correct += np.sum(query_batch[1] == predicted_labels)
            total_queries += query_batch[1].shape[0]
            if test_invariance:
                # Count queries and predictions with easy/distractor speakers
                for q_index in range(query_batch[1].shape[0]):
                    n_same_speaker = np.sum(
                        np.logical_and(query_batch[1][q_index] == support_batch[1],
                                       query_batch[2][q_index] == support_batch[2]))
                    if n_same_speaker > 0:  # easy speakers
#                         print("Support set labels:", support_batch[1])
#                         print("Support set speaker:", support_batch[2])
#                         print("Query set labels:", query_batch[1])
#                         print("Query set speaker:", query_batch[2])
                        total_easy_queries += 1
                        if query_batch[1][q_index] == predicted_labels[q_index]:
                            total_easy_correct += 1
                    else:  # distractor speakers
                        total_distractor_queries += 1
                        if query_batch[1][q_index] == predicted_labels[q_index]:
                            total_distractor_correct += 1
                # prediction_originators = support_batch[2][nearest_neighbour_indices]
                # total_easy_correct += np.sum(
                #     np.logical_and(query_batch[1] == predicted_labels,
                #                    query_batch[2] == prediction_originators))
                # total_easy_queries += np.sum(query_batch[2] == prediction_originators)
                # total_distractor_correct += np.sum(
                #     np.logical_and(query_batch[1] == predicted_labels,
                #                    query_batch[2] != prediction_originators))
                # total_distractor_queries += np.sum(query_batch[2] != prediction_originators)
            if episode % log_interval == 0:
                avg_acc = total_correct/total_queries
                ep_message = ("\tFew-shot Test: [Episode: {}/{}]\t"
                                "Average accuracy: {:.7f}".format(
                                    episode, n_episodes, avg_acc))
                if test_invariance:
                    avg_easy_acc = total_easy_correct/total_easy_queries if total_easy_queries != 0 else 0.
                    avg_dist_acc = total_distractor_correct/total_distractor_queries if total_distractor_queries != 0 else 0.
                    ep_message += ("\n\t\tEasy speaker accuracy: {:.7f}\tDistractor "
                                   "speaker accuracy: {:.7f}".format(
                                       avg_easy_acc, avg_dist_acc))
                    ep_message += ("\n\t\tNum easy speakers: {}\tNum distractor speakers: {}"
                                   .format(total_easy_queries, total_distractor_queries))
                logging.info(ep_message)
        # ----------------------------------------------------------------------
        # Print stats:
        # ----------------------------------------------------------------------
        avg_acc = total_correct/total_queries
        few_shot_message = ("Test set (few-shot): Average accuracy: "
                            "{:.5f}".format(avg_acc))
        if test_invariance:
            avg_easy_acc = total_easy_correct/total_easy_queries
            avg_dist_acc = total_distractor_correct/total_distractor_queries
            few_shot_message += ("\n\t\tEasy speaker accuracy: {:.5f}\tDistractor "
                                 "speaker accuracy: {:.5f}".format(
                                     avg_easy_acc, avg_dist_acc))
            few_shot_message += ("\n\t\tNum easy speakers: {}\tNum distractor speakers: {}"
                                 .format(total_easy_queries, total_distractor_queries))
        logging.info(few_shot_message)
        test_summ_val = sess.run(test_summ, feed_dict={test_acc_input: avg_acc})
        summary_writer.add_summary(test_summ_val, step)
        summary_writer.flush()
        with open(os.path.join(output_dir, 'test_result.txt'), 'w') as res_file:
            res_file.write(few_shot_message)
    # Testing complete
    logging.info("Testing complete.")


def _get_unpadded_image(image, pad_length=110):
    """Remove padding from an end-padded mfcc/fbank image."""
    for i in range(pad_length-1, -1, -1):
        pad_length = i
        if np.sum(image[:, pad_length]) != 0.:
            break
    return image[:, :(pad_length+1)]


if __name__ == '__main__':
    # Call the script main function
    main()
    print('Exitting ...')
