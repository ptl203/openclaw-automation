import requests
import json

def fetch_syracuse_lacrosse_stats():
    try:
        # Team info (record, rank, next game)
        url = "https://site.api.espn.com/apis/site/v2/sports/lacrosse/mens-college-lacrosse/teams/183"
        resp = requests.get(url).json()
        team = resp['team']
        
        record = "N/A"
        for item in team.get('record', {}).get('items', []):
            if item.get('type') == 'total':
                record = item.get('summary', 'N/A')
                break
                
        rank = team.get('curatedRank', {}).get('current', 'Unranked')
        
        next_game = "No upcoming game scheduled"
        if team.get('nextEvent'):
            next_game_event = team['nextEvent'][0]
            next_game_name = next_game_event.get('name', 'Unknown Opponent')
            next_game_date = next_game_event.get('competitions', [{}])[0].get('status', {}).get('type', {}).get('detail', 'Unknown Date')
            next_game = f"{next_game_name} ({next_game_date})"
            
        # Last result
        sched_url = "https://site.api.espn.com/apis/site/v2/sports/lacrosse/mens-college-lacrosse/teams/183/schedule"
        sched_resp = requests.get(sched_url).json()
        
        last_result = "No completed games found"
        completed_games = []
        for event in sched_resp.get('events', []):
            if event.get('competitions', [{}])[0].get('status', {}).get('type', {}).get('completed', False):
                completed_games.append(event)
                
        if completed_games:
            last_game = completed_games[-1]
            last_game_name = last_game.get('name', 'Unknown Game')
            competitors = last_game.get('competitions', [{}])[0].get('competitors', [])
            score_str = ""
            if len(competitors) == 2:
                team1 = competitors[0]
                team2 = competitors[1]
                t1_name = team1.get('team', {}).get('abbreviation', 'Team1')
                t1_score = team1.get('score', {}).get('displayValue', '0')
                t2_name = team2.get('team', {}).get('abbreviation', 'Team2')
                t2_score = team2.get('score', {}).get('displayValue', '0')
                score_str = f" - {t1_name} {t1_score}, {t2_name} {t2_score}"
            
            last_result = f"{last_game_name}{score_str}"
            
        return f"Record: {record}\nStandings Place (Rank): {rank}\nLast Result: {last_result}\nNext Game: {next_game}"
    except Exception as e:
        return f"Error fetching Syracuse stats: {e}"

print(fetch_syracuse_lacrosse_stats())
