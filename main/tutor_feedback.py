def tutor_feedback(move_type):

    if move_type == "blunder":
        return "Tutor: You left a piece undefended."

    elif move_type == "capture":
        return "Tutor: Great move, you gained material."

    elif move_type == "centre":
        return "Tutor: Strong centre control"

    elif move_type == "check":
        return "Tutor: Excellent pressure on the king."

    else:
        return "Tutor: Solid move."

move = input("Enter move type: ")
feedback = tutor_feedback(move)
print(feedback)