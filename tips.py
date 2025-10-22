def calculate_tips(total, tip_percentage, num_people ):
        final_total = (total * (tip_percentage / 100)) + total
        split_total = final_total/num_people

        return split_total