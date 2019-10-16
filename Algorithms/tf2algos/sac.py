import numpy as np
import tensorflow as tf
import tensorflow_probability as tfp
import Nn
from .policy import Policy


class SAC(Policy):
    def __init__(self,
                 s_dim,
                 visual_sources,
                 visual_resolution,
                 a_dim_or_list,
                 action_type,
                 gamma=0.99,
                 max_episode=50000,
                 batch_size=128,
                 buffer_size=10000,
                 use_priority=False,
                 n_step=False,
                 base_dir=None,

                 alpha=0.2,
                 ployak=0.995,
                 lr=5.0e-4,
                 auto_adaption=True,
                 logger2file=False,
                 out_graph=False):
        assert action_type == 'continuous', 'sac only support continuous action space'
        super().__init__(
            s_dim=s_dim,
            visual_sources=visual_sources,
            visual_resolution=visual_resolution,
            a_dim_or_list=a_dim_or_list,
            action_type=action_type,
            gamma=gamma,
            max_episode=max_episode,
            base_dir=base_dir,
            policy_mode='OFF',
            batch_size=batch_size,
            buffer_size=buffer_size,
            use_priority=use_priority,
            n_step=n_step)
        self.lr = tf.keras.optimizers.schedules.PolynomialDecay(lr, self.max_episode, 1e-10, power=1.0)
        self.ployak = ployak
        self.sigma_offset = np.full([self.a_counts, ], 0.01)
        self.log_alpha = alpha if not auto_adaption else tf.Variable(initial_value=0.0, name='log_alpha', dtype=tf.float64, trainable=True)
        self.auto_adaption = auto_adaption
        self.actor_net = Nn.actor_continuous(self.s_dim, self.visual_dim, self.a_counts, 'actor_net')
        self.q1_net = Nn.critic_q_one(self.s_dim, self.visual_dim, self.a_counts, 'q1_net')
        self.q2_net = Nn.critic_q_one(self.s_dim, self.visual_dim, self.a_counts, 'q2_net')
        self.v_net = Nn.critic_v(self.s_dim, self.visual_dim, 'v_net')
        self.v_target_net = Nn.critic_v(self.s_dim, self.visual_dim, 'v_target_net')
        self.update_target_net_weights(self.v_target_net.weights, self.v_net.weights)
        self.optimizer_critic = tf.keras.optimizers.Adam(learning_rate=self.lr(self.episode))
        self.optimizer_actor = tf.keras.optimizers.Adam(learning_rate=self.lr(self.episode))
        self.optimizer_alpha = tf.keras.optimizers.Adam(learning_rate=self.lr(self.episode))
        self.generate_recorder(
            logger2file=logger2file,
            model=self
        )
        self.recorder.logger.info('''
　　　　ｘｘｘｘｘｘｘ　　　　　　　　　　　ｘｘ　　　　　　　　　　　ｘｘｘｘｘｘ　　　　
　　　　ｘｘ　　　ｘｘ　　　　　　　　　　ｘｘｘ　　　　　　　　　　ｘｘｘ　　ｘｘ　　　　
　　　　ｘｘ　　　　ｘ　　　　　　　　　　ｘｘｘ　　　　　　　　　　ｘｘ　　　　ｘｘ　　　
　　　　ｘｘｘｘ　　　　　　　　　　　　　ｘ　ｘｘ　　　　　　　　　ｘｘ　　　　　　　　　
　　　　　ｘｘｘｘｘｘ　　　　　　　　　ｘｘ　ｘｘ　　　　　　　　ｘｘｘ　　　　　　　　　
　　　　　　　　ｘｘｘ　　　　　　　　　ｘｘｘｘｘｘ　　　　　　　ｘｘｘ　　　　　　　　　
　　　　ｘ　　　　ｘｘ　　　　　　　　ｘｘ　　　ｘｘ　　　　　　　　ｘｘ　　　　ｘｘ　　　
　　　　ｘｘ　　　ｘｘ　　　　　　　　ｘｘ　　　ｘｘ　　　　　　　　ｘｘｘ　　ｘｘｘ　　　
　　　　ｘｘｘｘｘｘｘ　　　　　　　ｘｘｘ　　ｘｘｘｘｘ　　　　　　　ｘｘｘｘｘｘ　　　　　　　　　　　　　　　　　　　　　　　　　　　　　　　　　　　　　　　　　　　　　　　　　　　　　　　　　　　　　　　　
        ''')

    def choose_action(self, s, visual_s):
        return self._get_action(s, visual_s)[-1].numpy()

    def choose_inference_action(self, s, visual_s):
        return self._get_action(s, visual_s)[0].numpy()

    @tf.function
    def _get_action(self, vector_input, visual_input):
        with tf.device(self.device):
            mu, sigma = self.actor_net(vector_input, visual_input)
            norm_dist = tfp.distributions.Normal(loc=mu, scale=sigma + self.sigma_offset)
            a_new = tf.clip_by_value(norm_dist.sample(), -1, 1)
        return mu, a_new

    def store_data(self, s, visual_s, a, r, s_, visual_s_, done):
        self.off_store(s, visual_s, a, r[:, np.newaxis], s_, visual_s_, done[:, np.newaxis])

    def learn(self, episode):
        self.episode = episode
        if self.data.is_lg_batch_size:
            s, visual_s, a, r, s_, visual_s_, done = self.data.sample()
            if self.use_priority:
                self.IS_w = self.data.get_IS_w()
            actor_loss, critic_loss, entropy, td_error = self.train(s, visual_s, a, r, s_, visual_s_, done)
            if self.use_priority:
                self.data.update(td_error, episode)
            self.update_target_net_weights(self.v_target_net.weights, self.v_net.weights, self.ployak)
            tf.summary.experimental.set_step(self.global_step)
            tf.summary.scalar('LOSS/actor_loss', actor_loss)
            tf.summary.scalar('LOSS/critic_loss', critic_loss)
            tf.summary.scalar('LOSS/alpha', tf.exp(self.log_alpha))
            tf.summary.scalar('LOSS/entropy', entropy)
            tf.summary.scalar('LEARNING_RATE/lr', self.lr(self.episode))
            self.recorder.writer.flush()

    @tf.function(experimental_relax_shapes=True)
    def train(self, s, visual_s, a, r, s_, visual_s_, done):
        done = tf.cast(done, tf.float64)
        with tf.device(self.device):
            with tf.GradientTape() as tape:
                mu, sigma = self.actor_net(s, visual_s)
                norm_dist = tfp.distributions.Normal(loc=mu, scale=sigma + self.sigma_offset)
                a_new = tf.clip_by_value(norm_dist.sample(), -1, 1)
                log_prob = norm_dist.log_prob(a_new)
                q1 = self.q1_net(s, visual_s, a)
                q2 = self.q2_net(s, visual_s, a)
                v = self.v_net(s, visual_s)
                q1_anew = self.q1_net(s, visual_s, a_new)
                q2_anew = self.q2_net(s, visual_s, a_new)
                v_target = self.v_target_net(s_, visual_s_)
                dc_r = tf.stop_gradient(r + self.gamma * v_target * (1 - done))
                v_from_q_stop = tf.stop_gradient(tf.minimum(q1_anew, q2_anew) - tf.exp(self.log_alpha) * log_prob)
                td_v = v - v_from_q_stop
                td_error1 = q1 - dc_r
                td_error2 = q2 - dc_r
                q1_loss = tf.reduce_mean(tf.square(td_error1) * self.IS_w)
                q2_loss = tf.reduce_mean(tf.square(td_error2) * self.IS_w)
                v_loss_stop = tf.reduce_mean(tf.square(td_v) * self.IS_w)
                critic_loss = 0.5 * q1_loss + 0.5 * q2_loss + 0.5 * v_loss_stop
            critic_grads = tape.gradient(critic_loss, self.q1_net.trainable_variables + self.q2_net.trainable_variables + self.v_net.trainable_variables)
            self.optimizer_critic.apply_gradients(
                zip(critic_grads, self.q1_net.trainable_variables + self.q2_net.trainable_variables + self.v_net.trainable_variables)
            )

            with tf.GradientTape() as tape:
                mu, sigma = self.actor_net(s, visual_s)
                norm_dist = tfp.distributions.Normal(loc=mu, scale=sigma + self.sigma_offset)
                entropy = tf.reduce_mean(norm_dist.entropy())
                a_new = tf.clip_by_value(norm_dist.sample(), -1, 1)
                log_prob = norm_dist.log_prob(a_new)
                q1_anew = self.q1_net(s, visual_s, a_new)
                actor_loss = -tf.reduce_mean(q1_anew - tf.exp(self.log_alpha) * log_prob)
            actor_grads = tape.gradient(actor_loss, self.actor_net.trainable_variables)
            self.optimizer_actor.apply_gradients(
                zip(actor_grads, self.actor_net.trainable_variables)
            )

            if self.auto_adaption:
                with tf.GradientTape() as tape:
                    mu, sigma = self.actor_net(s, visual_s)
                    norm_dist = tfp.distributions.Normal(loc=mu, scale=sigma + self.sigma_offset)
                    a_new = tf.clip_by_value(norm_dist.sample(), -1, 1)
                    log_prob = norm_dist.log_prob(a_new)
                    alpha_loss = -tf.reduce_mean(self.log_alpha * tf.stop_gradient(log_prob - self.a_counts))
                alpha_grads = tape.gradient(alpha_loss, [self.log_alpha])
                self.optimizer_alpha.apply_gradients(
                    zip(alpha_grads, [self.log_alpha])
                )
            self.global_step.assign_add(1)
            return actor_loss, critic_loss, entropy, td_error1

    @tf.function(experimental_relax_shapes=True)
    def train_persistent(self, s, visual_s, a, r, s_, visual_s_, done):
        done = tf.cast(done, tf.float64)
        with tf.device(self.device):
            with tf.GradientTape(persistent=True) as tape:
                mu, sigma = self.actor_net(s, visual_s)
                norm_dist = tfp.distributions.Normal(loc=mu, scale=sigma + self.sigma_offset)
                entropy = tf.reduce_mean(norm_dist.entropy())
                a_new = tf.clip_by_value(norm_dist.sample(), -1, 1)
                log_prob = norm_dist.log_prob(a_new)
                q1 = self.q1_net(s, visual_s, a)
                q2 = self.q2_net(s, visual_s, a)
                v = self.v_net(s, visual_s)
                q1_anew = self.q1_net(s, visual_s, a_new)
                q2_anew = self.q2_net(s, visual_s, a_new)
                v_target = self.v_target_net(s_, visual_s_)
                dc_r = tf.stop_gradient(r + self.gamma * v_target * (1 - done))
                v_from_q_stop = tf.stop_gradient(tf.minimum(q1_anew, q2_anew) - tf.exp(self.log_alpha) * log_prob)
                td_v = v - v_from_q_stop
                td_error1 = q1 - dc_r
                td_error2 = q2 - dc_r
                q1_loss = tf.reduce_mean(tf.square(td_error1) * self.IS_w)
                q2_loss = tf.reduce_mean(tf.square(td_error2) * self.IS_w)
                v_loss_stop = tf.reduce_mean(tf.square(td_v) * self.IS_w)
                critic_loss = 0.5 * q1_loss + 0.5 * q2_loss + 0.5 * v_loss_stop
                actor_loss = -tf.reduce_mean(q1_anew - tf.exp(self.log_alpha) * log_prob)
                if self.auto_adaption:
                    alpha_loss = -tf.reduce_mean(self.log_alpha * tf.stop_gradient(log_prob - self.a_counts))
            critic_grads = tape.gradient(critic_loss, self.q1_net.trainable_variables + self.q2_net.trainable_variables + self.v_net.trainable_variables)
            self.optimizer_critic.apply_gradients(
                zip(critic_grads, self.q1_net.trainable_variables + self.q2_net.trainable_variables + self.v_net.trainable_variables)
            )
            actor_grads = tape.gradient(actor_loss, self.actor_net.trainable_variables)
            self.optimizer_actor.apply_gradients(
                zip(actor_grads, self.actor_net.trainable_variables)
            )
            if self.auto_adaption:
                alpha_grads = tape.gradient(alpha_loss, [self.log_alpha])
                self.optimizer_alpha.apply_gradients(
                    zip(alpha_grads, [self.log_alpha])
                )
            self.global_step.assign_add(1)
            return actor_loss, critic_loss, entropy, td_error1
