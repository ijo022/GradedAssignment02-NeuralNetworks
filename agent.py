"""
store all the agents here
"""
from replay_buffer import ReplayBuffer, ReplayBufferNumpy
import numpy as np
import time
import pickle
from collections import deque, OrderedDict
import json

import math
import torch
import torch.nn as nn
from torch.nn import functional as F



# Changed from Tensorflow to Pytorch
def huber_loss(y_true, y_pred, delta=1):
    """Torch implementation for huber loss
    loss = {
        0.5 * (y_true - y_pred)**2 if abs(y_true - y_pred) < delta
        delta * (abs(y_true - y_pred) - 0.5 * delta) otherwise
    }
    Parameters
    ----------
    y_true : Tensor
        The true values for the regression data
    y_pred : Tensor
        The predicted values for the regression data
    delta : float, optional
        The cutoff to decide whether to use quadratic or linear loss

    Returns
    -------
    loss : Tensor
        loss values for all points
    """
    error = (y_true - y_pred)
    quad_error = 0.5 * torch.square(error)
    lin_error = delta * (torch.abs(error) - 0.5 * delta)
    # quadratic error, linear error
    return torch.where(torch.abs(error) < delta, quad_error, lin_error)

# Changed from Tensorflow to Pytorch
def mean_huber_loss(y_true, y_pred, delta=1):
    """Calculates the mean value of huber loss

    Parameters
    ----------
    y_true : Tensor
        The true values for the regression data
    y_pred : Tensor
        The predicted values for the regression data
    delta : float, optional
        The cutoff to decide whether to use quadratic or linear loss

    Returns
    -------
    loss : Tensor
        average loss across points
    """
    return torch.mean(huber_loss(y_true, y_pred, delta))


# Deep Q CNN Network for target
class DeepQCNN(nn.Module):
    # output in the form of actions (4 possible moves)
    """
    adding an nn.Module class for the model
    """
    def __init__(self):
        super(DeepQCNN, self).__init__()
        self.conv1 = nn.Conv2d(2, 16, (3, 3))
        self.conv2 = nn.Conv2d(16, 32, (3, 3))
        self.conv3 = nn.Conv2d(32, 64, (5, 5))
        self.flat = nn.Flatten()
        self.fc1 = nn.Linear(256, 64)
        self.out = nn.Linear(64, 4) 

    def forward(self, x):
        x = F.relu(self.conv1(x))
        x = F.relu(self.conv2(x))
        x = F.relu(self.conv3(x))
        x = self.flat(x)
        x = F.relu(self.fc1(x))
        x = self.out(x) 
        return x


# CNN for evaluation/critic network
class AAC_CNN(nn.Module):
    # output alog -logits (probability distribution over actions) and state values.
    def __init__(self, p):
        super(AAC_CNN, self).__init__()
        self.param = p
        self.conv1 = nn.Conv2d(2, 16, (3, 3))
        self.conv2 = nn.Conv2d(16, 32, (3, 3))
        self.flat = nn.Flatten()
        self.fc1 = nn.Linear(1152, 64)
        self.action_logits = nn.Linear(64, 4)
        self.state_value = nn.Linear(64, 1) 

    def forward(self, x):
        x = F.relu(self.conv1(x))
        x = F.relu(self.conv2(x))
        x = self.flat(x)
        x = F.relu(self.fc1(x))
        if self.param == "logits":
            return self.action_logits(x)
        elif self.param == "values":
            return self.state_value(x)
        elif self.param == "full":
            return self.action_logits(x), self.state_value(x)


class Agent():
    """Base class for all agents
    This class extends to the following classes
    DeepQLearningAgent
    HamiltonianCycleAgent
    BreadthFirstSearchAgent

    Attributes
    ----------
    _board_size : int
        Size of board, keep greater than 6 for useful learning
        should be the same as the env board size
    _n_frames : int
        Total frames to keep in history when making prediction
        should be the same as env board size
    _buffer_size : int
        Size of the buffer, how many examples to keep in memory
        should be large for DQN
    _n_actions : int
        Total actions available in the env, should be same as env
    _gamma : float
        Reward discounting to use for future rewards, useful in policy
        gradient, keep < 1 for convergence
    _use_target_net : bool
        If use a target network to calculate next state Q values,
        necessary to stabilise DQN learning
    _input_shape : tuple
        Tuple to store individual state shapes
    _board_grid : Numpy array
        A square filled with values from 0 to board size **2,
        Useful when converting between row, col and int representation
    _version : str
        model version string
    """

    def __init__(self, board_size=10, frames=2, buffer_size=10000,
                 gamma=0.99, n_actions=3, use_target_net=True,
                 version=''):
        """ initialize the agent

        Parameters
        ----------
        board_size : int, optional
            The env board size, keep > 6
        frames : int, optional
            The env frame count to keep old frames in state
        buffer_size : int, optional
            Size of the buffer, keep large for DQN
        gamma : float, optional
            Agent's discount factor, keep < 1 for convergence
        n_actions : int, optional
            Count of actions available in env
        use_target_net : bool, optional
            Whether to use target network, necessary for DQN convergence
        version : str, optional except NN based models
            path to the model architecture json
        """
        self._board_size = board_size
        self._n_frames = frames
        self._buffer_size = buffer_size
        self._n_actions = n_actions
        self._gamma = gamma
        self._use_target_net = use_target_net
        self._input_shape = (self._board_size, self._board_size, self._n_frames)
        # reset buffer also initializes the buffer
        self.reset_buffer()
        self._board_grid = np.arange(0, self._board_size ** 2) \
            .reshape(self._board_size, -1)
        self._version = version


    def get_gamma(self):
        """Returns the agent's gamma value

        Returns
        -------
        _gamma : float
            Agent's gamma value
        """
        return self._gamma

    def reset_buffer(self, buffer_size=None):
        """Reset current buffer

        Parameters
        ----------
        buffer_size : int, optional
            Initialize the buffer with buffer_size, if not supplied,
            use the original value
        """
        if (buffer_size is not None):
            self._buffer_size = buffer_size
        self._buffer = ReplayBufferNumpy(self._buffer_size, self._board_size,
                                         self._n_frames, self._n_actions)

    def get_buffer_size(self):
        """Get the current buffer size

        Returns
        -------
        buffer size : int
            Current size of the buffer
        """
        return self._buffer.get_current_size()

    def add_to_buffer(self, board, action, reward, next_board, done, legal_moves):
        """Add current game step to the replay buffer

        Parameters
        ----------
        board : Numpy array
            Current state of the board, can contain multiple games
        action : Numpy array or int
            Action that was taken, can contain actions for multiple games
        reward : Numpy array or int
            Reward value(s) for the current action on current states
        next_board : Numpy array
            State obtained after executing action on current state
        done : Numpy array or int
            Binary indicator for game termination
        legal_moves : Numpy array
            Binary indicators for actions which are allowed at next states
        """
        self._buffer.add_to_buffer(board, action, reward, next_board,
                                   done, legal_moves)

    def save_buffer(self, file_path='', iteration=None):
        """Save the buffer to disk

        Parameters
        ----------
        file_path : str, optional
            The location to save the buffer at
        iteration : int, optional
            Iteration number to tag the file name with, if None, iteration is 0
        """
        if (iteration is not None):
            assert isinstance(iteration, int), "iteration should be an integer"
        else:
            iteration = 0
        with open("{}/buffer_{:04d}".format(file_path, iteration), 'wb') as f:
            pickle.dump(self._buffer, f)

    def load_buffer(self, file_path='', iteration=None):
        """Load the buffer from disk

        Parameters
        ----------
        file_path : str, optional
            Disk location to fetch the buffer from
        iteration : int, optional
            Iteration number to use in case the file has been tagged
            with one, 0 if iteration is None

        Raises
        ------
        FileNotFoundError
            If the requested file could not be located on the disk
        """
        if (iteration is not None):
            assert isinstance(iteration, int), "iteration should be an integer"
        else:
            iteration = 0
        with open("{}/buffer_{:04d}".format(file_path, iteration), 'rb') as f:
            self._buffer = pickle.load(f)

    def _point_to_row_col(self, point):
        """Covert a point value to row, col value
        point value is the array index when it is flattened

        Parameters
        ----------
        point : int
            The point to convert

        Returns
        -------
        (row, col) : tuple
            Row and column values for the point
        """
        return (point // self._board_size, point % self._board_size)

    def _row_col_to_point(self, row, col):
        """Covert a (row, col) to value
        point value is the array index when it is flattened

        Parameters
        ----------
        row : int
            The row number in array
        col : int
            The column number in array
        Returns
        -------
        point : int
            point value corresponding to the row and col values
        """
        return row * self._board_size + col

#Changed Deep Q Learning agent from using Tensorflow to Pytorch module
class DeepQLearningAgent(Agent):
    """This agent learns the game via Q learning
    model outputs everywhere refers to Q values
    This class extends to the following classes
    PolicyGradientAgent
    AdvantageActorCriticAgent

    Attributes
    ----------
    _model : Torch Graph
        Stores the graph of the DQN model
    _target_net : Torch Graph
        Stores the target network graph of the DQN model
    """

    def __init__(self, board_size=10, frames=4, buffer_size=10000,
                 gamma=0.99, n_actions=3, use_target_net=True,
                 version=''):
        """Initializer for DQN agent, arguments are same as Agent class
        except use_target_net is by default True and we call and additional
        reset models method to initialize the DQN networks
        """
        Agent.__init__(self, board_size=board_size, frames=frames, buffer_size=buffer_size,
                       gamma=gamma, n_actions=n_actions, use_target_net=use_target_net,
                       version=version)
        self.reset_models()

    def reset_models(self):
        """ Reset all the models by creating new graphs"""
        self._model = self._agent_model()
        if (self._use_target_net):
            self._target_net = self._agent_model()
            self.update_target_net()
        #self.optimizer = torch.optim.RMSprop(self._model.parameters(), lr=0.0005)

    def _prepare_input(self, board):
        """Reshape input and normalize

        Parameters
        ----------
        board : Numpy array
            The board state to process

        Returns
        -------
        board : Numpy array
            Processed and normalized board
        """
        if (board.ndim == 3):
            board = board.reshape((1,) + self._input_shape)

        # rolling the axis to change shape (64, 2, 10, 10) to (64, 10, 10, 2)
        board = np.rollaxis(board, 3, 1)

        board = self._normalize_board(board.copy())
        return board.copy()

    def _get_model_outputs(self, board, model=None):
        """Get action values from the DQN model

        Parameters
        ----------
        board : Numpy array
            The board state for which to predict action values
        model : Torch Graph, optional
            The graph to use for prediction, model or target network

        Returns
        -------
        model_outputs : Numpy array
            Predicted model outputs on board,
            of shape board.shape[0] * num actions
        """
        # to correct dimensions and normalize
        board = torch.Tensor(self._prepare_input(board))
        if model is None:
            model = self._model
        return model(board).detach().numpy()

    def _normalize_board(self, board):
        """Normalize the board before input to the network

        Parameters
        ----------
        board : Numpy array
            The board state to normalize

        Returns
        -------
        board : Numpy array
            The copy of board state after normalization
        """
        # return board.copy()
        # return((board/128.0 - 1).copy())
        return board.astype(np.float32) / 4.0

    def move(self, board, legal_moves, value=None):
        """Get the action with maximum Q value

        Parameters
        ----------
        board : Numpy array
            The board state on which to calculate best action
        value : None, optional
            Kept for consistency with other agent classes

        Returns
        -------
        output : Numpy array
            Selected action using the argmax function
        """
        # use the agent model to make the predictions
        model_outputs = self._get_model_outputs(board, self._model)
        return np.argmax(np.where(legal_moves == 1, model_outputs, -np.inf), axis=1)

    def _agent_model(self):
        """Returns the model which evaluates Q values for a given state input

        Returns
        -------
        model : Torch Graph
            DQN model graph
        """
        # using the layers from json file v17.1
        model = DeepQCNN()
        self.optimizer = torch.optim.RMSprop(model.parameters(), lr=0.0005)
        return model

    def set_weights_trainable(self):
        """Set selected layers to non trainable and compile the model"""
        for layer in self._model.layers:
            layer.trainable = False
        # the last dense layers should be trainable
        for s in ['action_prev_dense', 'action_values']:
            self._model.get_layer(s).trainable = True
        '''self._model.compile(optimizer=self._model.optimizer,
                            loss=self._model.loss)
        '''
        optimizer = self._model.optimizer
        loss = self._model.loss
        return optimizer, loss

    def get_action_proba(self, board, values=None):
        """Returns the action probability values using the DQN model

        Parameters
        ----------
        board : Numpy array
            Board state on which to calculate action probabilities
        values : None, optional
            Kept for consistency with other agent classes

        Returns
        -------
        model_outputs : Numpy array
            Action probabilities, shape is board.shape[0] * n_actions
        """
        model_outputs = self._get_model_outputs(board, self._model)
        # subtracting max and taking softmax does not change output
        # do this for numerical stability
        model_outputs = np.clip(model_outputs, -10, 10)
        model_outputs = model_outputs - model_outputs.max(axis=1).reshape((-1, 1))
        model_outputs = np.exp(model_outputs)
        model_outputs = model_outputs / model_outputs.sum(axis=1).reshape((-1, 1))
        return model_outputs

    def save_model(self, file_path='', iteration=None):
        """Save the current models to disk using Torch's
        inbuilt save model function (saves in h5 format)
        saving weights instead of model as cannot load compiled
        model with any kind of custom object (loss or metric)

        Parameters
        ----------
        file_path : str, optional
            Path where to save the file
        iteration : int, optional
            Iteration number to tag the file name with, if None, iteration is 0
        """

        # Iteration changed to saving model in pytorch, but using h5 type file
        if (iteration is not None):
            assert isinstance(iteration, int), "iteration should be an integer"
        else:
            iteration = 0
        torch.save(self._model.state_dict(), "{}/model_{:04d}.pt".format(file_path, iteration))
        # self._model.save_weights("{}/model_{:04d}.h5".format(file_path, iteration))
        if (self._use_target_net):
            torch.save(self._target_net.state_dict(), "{}/model_{:04d}_target.pt".format(file_path, iteration))
            #self._target_net.save_weights("{}/model_{:04d}_target.h5".format(file_path, iteration))

    #Similarily using torch to load model
    def load_model(self, file_path='', iteration=None):
        """ load any existing models, if available """
        """Load models from disk using Torch's
        inbuilt load model function (model saved in h5 format)
        
        Parameters
        ----------
        file_path : str, optional
            Path where to find the file
        iteration : int, optional
            Iteration number the file is tagged with, if None, iteration is 0

        Raises
        ------
        FileNotFoundError
            The file is not loaded if not found and an error message is printed,
            this error does not affect the functioning of the program
        """
        if (iteration is not None):
            assert isinstance(iteration, int), "iteration should be an integer"
        else:
            iteration = 0
        self._model.load_state_dict(torch.load("{}/model_{:04d}.pt".format(file_path, iteration)))
        if (self._use_target_net):
            self._target_net.load_state_dict(torch.load("{}/model_{:04d}_target.pt".format(file_path, iteration)))

    def print_models(self):
        """Print the current models using summary method"""
        print('Training Model')
        print(self._model.summary())
        if (self._use_target_net):
            print('Target Network')
            print(self._target_net.summary())

    # Training the Deep Q Learning Agent
    def train_agent(self, batch_size=32, num_games=1, reward_clip=False):

        criterion = mean_huber_loss
        optimizer = torch.optim.RMSprop(self._model.parameters(), lr=0.0005)

        # Setting up transition samles for replay buffer
        s, a, r, next_s, done, legal_moves = self._buffer.sample(batch_size)
        if (reward_clip):
            r = np.sign(r)
        # calculate the discounted reward, and then train accordingly
        current_model = self._target_net if self._use_target_net else self._model

        next_model_outputs = self._get_model_outputs(next_s, current_model)
        # our estimate of expected future discounted reward
        discounted_reward = r + \
                            (self._gamma * np.max(np.where(legal_moves == 1, next_model_outputs, -np.inf),
                                                  axis=1).reshape(-1, 1)) * (1 - done)
        # create the target variable, only the column with action has different value
        target = self._get_model_outputs(s)
        # we bother only with the difference in reward estimate at the selected action
        target = (1 - a) * target + a * discounted_reward

        # Compute loss
        loss = criterion(torch.Tensor(self._model(torch.Tensor(self._prepare_input(s)))), torch.Tensor(target))
        
        # Zero out the gradients
        self.optimizer.zero_grad()

        # Optimizing steps (backward pass and gradient update)
        loss.backward()
        self.optimizer.step()
        return loss.detach().numpy()

    # Target network updates here:
    def update_target_net(self):

        """Update the weights of the target network, which is kept
        static for a few iterations to stabilize the other network.
        This should not be updated very frequently
        """
        if (self._use_target_net):
            m_dict = self._model.state_dict()
            t_dict = self._target_net.state_dict()
            for key in m_dict.keys():
                t_dict[key] = m_dict[key]

    def compare_weights(self):
        """Simple utility function to heck if the model and target
        network have the same weights or not
        """
        for i in range(len(self._model.layers)):
            for j in range(len(self._model.layers[i].weights)):
                c = (self._model.layers[i].weights[j].numpy() == \
                     self._target_net.layers[i].weights[j].numpy()).all()
                print('Layer {:d} Weights {:d} Match : {:d}'.format(i, j, int(c)))

    def copy_weights_from_agent(self, agent_for_copy):
        """Update weights between competing agents which can be used
        in parallel training
        """
        assert isinstance(agent_for_copy, self), "Agent type is required for copy"

        self._model.set_weights(agent_for_copy._model.get_weights())
        self._target_net.set_weights(agent_for_copy._model_pred.get_weights())


class AdvantageActorCriticAgent(DeepQLearningAgent):
    """This agent uses the Advantage Actor Critic method to train
    the reinforcement learning agent, we will use Q actor critic here

    Attributes
    ----------
    _action_values_model : Torch Graph
        Contains the network for the action values calculation model
    _actor_update : function
        Custom function to prepare the
    """

    def __init__(self, board_size=10, frames=4, buffer_size=10000,
                 gamma=0.99, n_actions=3, use_target_net=True,
                 version=''):
        DeepQLearningAgent.__init__(self, board_size=board_size, frames=frames,
                                    buffer_size=buffer_size, gamma=gamma,
                                    n_actions=n_actions, use_target_net=use_target_net,
                                    version=version)
        # Changed to Pytorch
        self._optimizer = torch.optim.RMSprop



    def _agent_model(self):
        """Returns the models which evaluate prob logits and action values
        for a given state input, Model is compiled in a different function
        Overrides parent

        Returns
        -------
        model_logits : Torch Graph
            A2C model graph for action logits
        model_full : Torch Graph
            A2C model complete graph
        """

        model_logits = AAC_CNN("logits")
        model_values = AAC_CNN("values")
        model_full = AAC_CNN("full")
        """input_board = Input((self._board_size, self._board_size, self._n_frames,))
        x = Conv2D(16, (3, 3), activation='relu', data_format='channels_last')(input_board)
        x = Conv2D(32, (3, 3), activation='relu', data_format='channels_last')(x)
        x = Flatten()(x)
        x = Dense(64, activation='relu', name='dense')(x)
        action_logits = Dense(self._n_actions, activation='linear', name='action_logits')(x)
        state_values = Dense(1, activation='linear', name='state_values')(x)

        model_logits = Model(inputs=input_board, outputs=action_logits)
        model_full = Model(inputs=input_board, outputs=[action_logits, state_values])
        model_values = Model(inputs=input_board, outputs=state_values)"""
        # updates are calculated in the train_agent function

        return model_logits, model_full, model_values

    def reset_models(self):
        """ Reset all the models by creating new graphs"""
        self._model, self._full_model, self._values_model = self._agent_model()
        if (self._use_target_net):
            _, _, self._target_net = self._agent_model()
            self.update_target_net()

    def save_model(self, file_path='', iteration=None):
        """Save the current models to disk using Torch's
        inbuilt save model function (saves in h5 format)
        saving weights instead of model as cannot load compiled
        model with any kind of custom object (loss or metric)

        Parameters
        ----------
        file_path : str, optional
            Path where to save the file
        iteration : int, optional
            Iteration number to tag the file name with, if None, iteration is 0
        """
        if (iteration is not None):
            assert isinstance(iteration, int), "iteration should be an integer"
        else:
            iteration = 0

        torch.save(self._model.state_dict(), "{}/model_{:04d}.pt".format(file_path, iteration))
        torch.save(self._full_model.state_dict(), "{}/model_{:04d}_full.pt".format(file_path, iteration))
        # self._model.save_weights("{}/model_{:04d}.h5".format(file_path, iteration))
        # self._full_model.save_weights("{}/model_{:04d}_full.h5".format(file_path, iteration))
        if (self._use_target_net):
            torch.save(self._values_model.state_dict(), "{}/model_{:04d}_values.pt".format(file_path, iteration))
            torch.save(self._target_net.state_dict(), "{}/model_{:04d}_target.pt".format(file_path, iteration))

            # self._values_model.save_weights("{}/model_{:04d}_values.h5".format(file_path, iteration))
            # self._target_net.save_weights("{}/model_{:04d}_target.h5".format(file_path, iteration))


    def load_model(self, file_path='', iteration=None):
        """ Load existing models based on file_path """
        """ Saved models put in h5 format.
            Mapping each layer to its parameter tensor with load_state_dict 
            ref https://pytorch.org/tutorials/beginner/saving_loading_models.html

        """
        if iteration is not None:
            assert isinstance(iteration, int), "iteration should be an integer"
        else:
            iteration = 0
        self._model.load_state_dict(torch.load("{}/model_{:04d}.pt".format(file_path, iteration)))
        self._full_model.load_state_dict(torch.load("{}/model_{:04d}_full.pt".format(file_path, iteration)))
        # self._model.load_weights("{}/model_{:04d}.h5".format(file_path, iteration))
        # self._full_model.load_weights("{}/model_{:04d}_full.h5".format(file_path, iteration))
        if (self._use_target_net):
            self._values_model.load_state_dict(torch.load("{}/model_{:04d}_values.pt".format(file_path, iteration)))
            self._target_net.load_state_dict(torch.load("{}/model_{:04d}_target.pt".format(file_path, iteration)))
            # self._values_model.load_weights("{}/model_{:04d}_values.h5".format(file_path, iteration))
            # self._target_net.load_weights("{}/model_{:04d}_target.h5".format(file_path, iteration))

    def update_target_net(self):
        """Updating the weights of the network
            ref https://pytorch.org/tutorials/beginner/saving_loading_models.html

        """
        if (self._use_target_net):
            model_dict = self._model.state_dict()
            target_dict = self._target_net.state_dict()
            for key in model_dict.keys():
                target_dict[key] = model_dict[key]
            # self._target_net.set_weights(self._values_model.get_weights())


    def train_agent(self, batch_size=32, beta=0.001, normalize_rewards=False,
                    num_games=1, reward_clip=False):
        """Train the model by sampling from buffer and return the error
        The buffer is assumed to contain all states of a finite set of games
        and is fully sampled from the buffer
        Overrides parent

        Parameters
        ----------
        batch_size : int, optional
            Not used here, kept for consistency with other agents
        beta : float, optional
            The weight for the policy gradient entropy loss
        normalize_rewards : bool, optional
            Whether to normalize rewards for stable training
        num_games : int, optional
            Not used here, kept for consistency with other agents
        reward_clip : bool, optional
            Not used here, kept for consistency with other agents

        Returns
        -------
        error : list
            The current loss (total loss, actor loss, critic loss)
        """
        # in policy gradient, only one complete episode is used for training
        s, a, r, next_s, done, _ = self._buffer.sample(self._buffer.get_current_size())
        s_prepared = self._prepare_input(s)
        next_s_prepared = self._prepare_input(next_s)
        # unlike DQN, the discounted reward is not estimated
        # we have defined custom actor and critic losses functions above
        # use that to train to agent model

        # normzlize the rewards for training stability, does not work in practice
        if (normalize_rewards):
            if ((r == r[0][0]).sum() == r.shape[0]):
                # std dev is zero
                r -= r
            else:
                r = (r - np.mean(r)) / np.std(r)

        if (reward_clip):
            r = np.sign(r)

        # calculate V values
        if (self._use_target_net):
            next_s_pred = self._target_net(torch.Tensor(next_s_prepared)).detach().numpy()
        else:
            next_s_pred = self._values_model(torch.Tensor(next_s_prepared)).detach().numpy()
        s_pred = self._values_model(torch.Tensor(s_prepared)).detach().numpy()

        # prepare target
        future_reward = self._gamma * next_s_pred * (1 - done)
        # calculate target for actor (uses advantage), similar to Policy Gradient
        advantage = torch.Tensor(a * (r + future_reward - s_pred))

        # calculate target for critic, simply current reward + future expected reward
        critic_target = r + future_reward

        model = self._full_model
        model_out = model(torch.Tensor(s_prepared))
        policy = F.softmax(model_out[0])
        log_policy = F.log_softmax(model_out[0])
        optimizer = torch.optim.RMSprop(model.parameters(), lr=0.0005)

        # calculate loss
        J = torch.sum(torch.multiply(advantage, log_policy)) / num_games
        entropy = -torch.sum(torch.multiply(policy, log_policy)) / num_games
        actor_loss = -J - beta * entropy
        critic_loss = mean_huber_loss(torch.Tensor(critic_target), model_out[1])
        loss = actor_loss + critic_loss
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()
        loss = [loss.detach().numpy(), actor_loss.detach().numpy(), critic_loss.detach().numpy()]
        return loss[0] if len(loss) == 1 else loss