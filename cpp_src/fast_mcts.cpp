#include <pybind11/pybind11.h>
#include <pybind11/numpy.h>
#include <pybind11/stl.h>
#include <pybind11/functional.h>
#include <vector>
#include <unordered_set>
#include <string>
#include <cmath>
#include <algorithm>
#include <iostream>

namespace py = pybind11;

class FastGoBoard {
public:
    int size = 9;
    std::vector<int> board;
    int current_player;
    std::unordered_set<std::string> history;
    int passes;
    bool done;

    // 维护前两手的历史棋盘，用于让 AI 拥有时间连贯性
    std::vector<int> last_board;
    std::vector<int> second_last_board;

    FastGoBoard() {
        board.assign(size * size, 0);
        last_board.assign(size * size, 0);
        second_last_board.assign(size * size, 0);
        current_player = 1; 
        passes = 0;
        done = false;
        history.insert(get_board_hash(board));
    }

    FastGoBoard(const FastGoBoard& other) = default;

    std::string get_board_hash(const std::vector<int>& b) const {
        std::string hash_str = "";
        for (int v : b) {
            if (v == 1) hash_str += "X";
            else if (v == -1) hash_str += "O";
            else hash_str += ".";
        }
        return hash_str;
    }

    std::vector<int> get_neighbors(int r, int c) const {
        std::vector<int> neighbors;
        int dr[] = {-1, 1, 0, 0};
        int dc[] = {0, 0, -1, 1};
        for (int i = 0; i < 4; ++i) {
            int nr = r + dr[i], nc = c + dc[i];
            if (nr >= 0 && nr < size && nc >= 0 && nc < size) {
                neighbors.push_back(nr * size + nc);
            }
        }
        return neighbors;
    }

    void get_group_and_liberties(const std::vector<int>& b, int idx, std::vector<int>& group, std::vector<int>& liberties) const {
        int color = b[idx];
        if (color == 0) return;

        std::vector<int> queue = {idx};
        std::vector<bool> visited(size * size, false);
        std::vector<bool> lib_visited(size * size, false);
        
        visited[idx] = true;
        group.push_back(idx);

        int head = 0;
        while (head < queue.size()) {
            int curr = queue[head++];
            int r = curr / size;
            int c = curr % size;

            for (int n_idx : get_neighbors(r, c)) {
                if (b[n_idx] == 0) {
                    if (!lib_visited[n_idx]) {
                        liberties.push_back(n_idx);
                        lib_visited[n_idx] = true;
                    }
                } else if (b[n_idx] == color && !visited[n_idx]) {
                    visited[n_idx] = true;
                    queue.push_back(n_idx);
                    group.push_back(n_idx);
                }
            }
        }
    }

    bool simulate_step(int idx, int player, std::vector<int>& sim_board) const {
        if (sim_board[idx] != 0) return false; 
        sim_board[idx] = player;
        int opponent = -player;

        int r = idx / size;
        int c = idx % size;
        for (int n_idx : get_neighbors(r, c)) {
            if (sim_board[n_idx] == opponent) {
                std::vector<int> opp_group, opp_liberties;
                get_group_and_liberties(sim_board, n_idx, opp_group, opp_liberties);
                if (opp_liberties.empty()) { 
                    for (int g_idx : opp_group) sim_board[g_idx] = 0;
                }
            }
        }

        std::vector<int> my_group, my_liberties;
        get_group_and_liberties(sim_board, idx, my_group, my_liberties);
        if (my_liberties.empty()) return false; 

        std::string new_hash = get_board_hash(sim_board);
        if (history.find(new_hash) != history.end()) return false;

        return true;
    }

    std::vector<int> get_legal_moves() const {
        std::vector<int> legal(size * size + 1, 0);
        legal[size * size] = 1; 
        for (int i = 0; i < size * size; ++i) {
            if (board[i] == 0) {
                std::vector<int> sim_board = board;
                if (simulate_step(i, current_player, sim_board)) legal[i] = 1;
            }
        }
        return legal;
    }

    void step(int action) {
        // 更新历史棋盘
        second_last_board = last_board;
        last_board = board;

        if (action == size * size) { 
            passes++;
            if (passes >= 2) done = true;
        } else {
            passes = 0;
            std::vector<int> next_board = board;
            simulate_step(action, current_player, next_board); 
            board = next_board;
            history.insert(get_board_hash(board));
        }
        current_player = -current_player;
    }

    // ==========================================
    // 提取 9 通道物理特征矩阵 (扁平化返回)
    // ==========================================
    std::vector<float> get_features() const {
        std::vector<float> features(9 * size * size, 0.0f);
        int area = size * size;
        std::vector<int> legal_moves = get_legal_moves();

        // 预先计算所有棋子的气
        std::vector<int> liberties_count(area, 0);
        std::vector<bool> checked(area, false);
        for (int i = 0; i < area; ++i) {
            if (board[i] != 0 && !checked[i]) {
                std::vector<int> group, libs;
                get_group_and_liberties(board, i, group, libs);
                int num_libs = libs.size();
                for (int g_idx : group) {
                    liberties_count[g_idx] = num_libs;
                    checked[g_idx] = true;
                }
            }
        }

        for (int i = 0; i < area; ++i) {
            // 通道 0: 我方棋子
            if (board[i] == current_player) features[0 * area + i] = 1.0f;
            // 通道 1: 敌方棋子
            if (board[i] == -current_player) features[1 * area + i] = 1.0f;
            
            // 通道 2, 3, 4: 气的数量 (非常强烈的存活/危险信号)
            if (board[i] != 0) {
                if (liberties_count[i] == 1) features[2 * area + i] = 1.0f; // 打吃！危！
                else if (liberties_count[i] == 2) features[3 * area + i] = 1.0f;
                else if (liberties_count[i] >= 3) features[4 * area + i] = 1.0f;
            }

            // 通道 5: 合法落子点
            if (legal_moves[i] == 1) features[5 * area + i] = 1.0f;

            // 通道 6: 上一手
            if (last_board[i] == current_player) features[6 * area + i] = 1.0f;
            else if (last_board[i] == -current_player) features[6 * area + i] = -1.0f;

            // 通道 7: 上二手
            if (second_last_board[i] == current_player) features[7 * area + i] = 1.0f;
            else if (second_last_board[i] == -current_player) features[7 * area + i] = -1.0f;

            // 通道 8: 当前玩家 (全盘统一填充)
            features[8 * area + i] = (current_player == 1) ? 1.0f : 0.0f;
        }

        return features;
    }
};

// ==========================================================
// MCTS 节点与搜索逻辑
// ==========================================================
class CppNode {
public:
    CppNode* parent;
    std::vector<CppNode*> children;
    std::vector<int> legal_actions;
    int action_taken; 
    int visits;
    double Q;
    double W;
    double P;

    CppNode(CppNode* p, double prior_prob, int action) 
        : parent(p), P(prior_prob), action_taken(action), visits(0), Q(0.0), W(0.0) {}
    ~CppNode() { for (auto child : children) delete child; }

    void expand(const std::vector<double>& action_probs, const std::vector<int>& legal_moves) {
        for (int i = 0; i < action_probs.size(); ++i) {
            if (legal_moves[i] == 1) {
                children.push_back(new CppNode(this, action_probs[i], i));
                legal_actions.push_back(i);
            }
        }
    }

    CppNode* select(double c_puct) {
        CppNode* best_child = nullptr;
        double best_puct = -1e9;
        for (auto child : children) {
            double U = c_puct * child->P * std::sqrt(visits) / (1.0 + child->visits);
            double puct_score = child->Q + U;
            if (puct_score > best_puct) {
                best_puct = puct_score;
                best_child = child;
            }
        }
        return best_child;
    }

    void backup(double value) {
        visits++; W += value; Q = W / visits;
        if (parent != nullptr) parent->backup(-value); 
    }
    bool is_expanded() const { return !children.empty(); }
};

class FastMCTS {
public:
    int num_simulations;
    double c_puct;
    py::function py_predict; 

    FastMCTS(int sims, double puct, py::function predict_fn) 
        : num_simulations(sims), c_puct(puct), py_predict(predict_fn) {}

    std::vector<double> get_action_prob(const FastGoBoard& env, double temp) {
        CppNode* root = new CppNode(nullptr, 1.0, -1);

        for (int i = 0; i < num_simulations; ++i) {
            CppNode* node = root;
            FastGoBoard sim_env = env; 

            while (node->is_expanded()) {
                node = node->select(c_puct);
                sim_env.step(node->action_taken);
            }

            if (sim_env.done) {
                node->backup(-1.0); 
                continue;
            }

            // 直接让 C++ 算出 9 通道特征，丢给 Python！
            std::vector<float> features = sim_env.get_features();
            py::array_t<float> py_features = py::cast(features);
            
            py::tuple result = py_predict(py_features); 
            
            std::vector<double> policy = result[0].cast<std::vector<double>>();
            double value = result[1].cast<double>();

            std::vector<int> legal_moves = sim_env.get_legal_moves();
            double sum_prob = 0.0;
            for(int a=0; a<policy.size(); ++a) {
                policy[a] *= legal_moves[a];
                sum_prob += policy[a];
            }
            if (sum_prob > 0) {
                for(int a=0; a<policy.size(); ++a) policy[a] /= sum_prob;
            } else { 
                double count = std::count(legal_moves.begin(), legal_moves.end(), 1);
                for(int a=0; a<policy.size(); ++a) policy[a] = legal_moves[a] / count;
            }

            node->expand(policy, legal_moves);
            node->backup(-value);
        }

        std::vector<double> action_probs(env.size * env.size + 1, 0.0);
        for (auto child : root->children) action_probs[child->action_taken] = child->visits;

        if (temp == 0.0) {
            int best_action = root->children[0]->action_taken;
            int max_visits = root->children[0]->visits;
            for (auto child : root->children) {
                if (child->visits > max_visits) {
                    max_visits = child->visits;
                    best_action = child->action_taken;
                }
            }
            std::fill(action_probs.begin(), action_probs.end(), 0.0);
            action_probs[best_action] = 1.0;
        } else {
            double sum = 0.0;
            for(double& p : action_probs) { p = std::pow(p, 1.0 / temp); sum += p; }
            for(double& p : action_probs) p /= sum;
        }

        delete root; 
        return action_probs;
    }
};

PYBIND11_MODULE(cgo, m) {
    py::class_<FastGoBoard>(m, "FastGoBoard")
        .def(py::init<>())
        .def_readwrite("size", &FastGoBoard::size)
        .def_readwrite("current_player", &FastGoBoard::current_player)
        .def_readwrite("done", &FastGoBoard::done)
        .def("get_legal_moves", &FastGoBoard::get_legal_moves)
        .def("step", &FastGoBoard::step)
        .def("get_features", &FastGoBoard::get_features) // 暴露新方法给 Python 收集数据用
        .def("get_board_2d", [](const FastGoBoard& b) {
            std::vector<std::vector<int>> board2d(b.size, std::vector<int>(b.size));
            for(int r=0; r<b.size; ++r)
                for(int c=0; c<b.size; ++c) board2d[r][c] = b.board[r * b.size + c];
            return board2d;
        });

    py::class_<FastMCTS>(m, "FastMCTS")
        .def(py::init<int, double, py::function>()) 
        .def("get_action_prob", &FastMCTS::get_action_prob);
}